"""多用户 / 会话 / token 用量存储层（SQLite）。

所有数据持久化到 ``$EMPEROR_DATA_DIR/emperor.db``（容器内挂数据卷，跨重建不丢）。
零外部依赖，仅用标准库 ``sqlite3`` / ``hashlib`` / ``secrets``。

设计要点
--------
* 用户：``users``（密码用 pbkdf2_hmac 加盐哈希，绝不存明文）；首个注册用户自动成为 admin。
* 会话：``sessions``（登录后签发的随机 token，可查看登录状态 / 最后活跃）。
* 对话：``conversations`` + ``messages``（多会话、可回看历史）。
* 用量：``token_ledger``（每次对话累计 prompt/completion token，供「token 消耗」面板展示）。

线程安全：写操作走全局 ``_lock``；连接 ``check_same_thread=False`` 供 FastAPI 多线程复用。
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import threading
from typing import Any, Optional

# ── 路径 ──
def _db_path() -> str:
    data_dir = os.getenv("EMPEROR_DATA_DIR", "/app/data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "emperor.db")


_conn: Optional[sqlite3.Connection] = None
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(_db_path(), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init_db() -> None:
    """创建所有表（幂等）。在应用启动时调用一次。"""
    with _lock:
        c = _get_conn()
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                username     TEXT UNIQUE NOT NULL,
                pw_hash      TEXT NOT NULL,
                pw_salt      TEXT NOT NULL,
                is_admin     INTEGER NOT NULL DEFAULT 0,
                created_at   REAL NOT NULL,
                last_active  REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                created_at  REAL NOT NULL,
                last_active REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                title       TEXT NOT NULL DEFAULT '新对话',
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS messages (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id   INTEGER NOT NULL,
                role              TEXT NOT NULL,
                content           TEXT NOT NULL,
                tokens_prompt     INTEGER NOT NULL DEFAULT 0,
                tokens_completion INTEGER NOT NULL DEFAULT 0,
                created_at        REAL NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS token_ledger (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                at          REAL NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_sess_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_ledger_user ON token_ledger(user_id);
            """
        )
        c.commit()


# ── 密码哈希 ──
def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000).hex()


def _now() -> float:
    return __import__("time").time()


# ── 用户 ──
def create_user(username: str, password: str, is_admin: bool = False) -> int:
    username = username.strip()
    if not username or not password:
        raise ValueError("用户名与密码均不能为空")
    salt = secrets.token_bytes(16)
    pw_hash = _hash_password(password, salt)
    with _lock:
        c = _get_conn()
        # 首个注册用户自动成为 admin（兜底管理入口）
        admin_flag = 1 if (is_admin or _count_users_unsafe(c) == 0) else 0
        cur = c.execute(
            "INSERT INTO users (username, pw_hash, pw_salt, is_admin, created_at, last_active) "
            "VALUES (?,?,?,?,?,?)",
            (username, pw_hash, salt.hex(), admin_flag, _now(), _now()),
        )
        c.commit()
        return int(cur.lastrowid)


def _count_users_unsafe(c: sqlite3.Connection) -> int:
    return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def verify_user(username: str, password: str) -> Optional[dict]:
    with _lock:
        c = _get_conn()
        row = c.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
        if row is None:
            return None
        salt = bytes.fromhex(row["pw_salt"])
        if _hash_password(password, salt) != row["pw_hash"]:
            return None
        c.execute("UPDATE users SET last_active=? WHERE id=?", (_now(), row["id"]))
        c.commit()
        return _row_to_user(row)


def get_user(user_id: int) -> Optional[dict]:
    with _lock:
        row = _get_conn().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None


def _row_to_user(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
        "created_at": row["created_at"],
        "last_active": row["last_active"],
    }


def ensure_admin(username: str, password: str) -> int:
    """幂等确保单一管理员账号存在（单用户部署用）。

    - 账号不存在 → 以 admin 身份创建；
    - 已存在 → 确保其 ``is_admin=1``，但**不覆盖**既有密码（避免每次重启重置用户口令）。
    - 返回该用户 id。
    """
    username = (username or "admin").strip()
    password = password or "changeme"
    with _lock:
        c = _get_conn()
        row = c.execute("SELECT id, is_admin FROM users WHERE username=?", (username,)).fetchone()
        if row is not None:
            if not row["is_admin"]:
                c.execute("UPDATE users SET is_admin=1 WHERE id=?", (row["id"],))
                c.commit()
            return int(row["id"])
        salt = secrets.token_bytes(16)
        pw_hash = _hash_password(password, salt)
        cur = c.execute(
            "INSERT INTO users (username, pw_hash, pw_salt, is_admin, created_at, last_active) "
            "VALUES (?,?,?,1,?,?)",
            (username, pw_hash, salt.hex(), _now(), _now()),
        )
        c.commit()
        return int(cur.lastrowid)


# ── 会话 token ──
def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _lock:
        c = _get_conn()
        c.execute(
            "INSERT INTO sessions (token, user_id, created_at, last_active) VALUES (?,?,?,?)",
            (token, user_id, _now(), _now()),
        )
        c.commit()
    return token


def is_session_valid(token: str) -> Optional[int]:
    """返回 user_id；无效/过期返回 None。同时刷新 last_active。"""
    if not token:
        return None
    with _lock:
        c = _get_conn()
        row = c.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()
        if row is None:
            return None
        c.execute("UPDATE sessions SET last_active=? WHERE token=?", (_now(), token))
        c.commit()
        return int(row["user_id"])


def get_session_user(token: str) -> Optional[dict]:
    uid = is_session_valid(token)
    return get_user(uid) if uid else None


def delete_session(token: str) -> None:
    if not token:
        return
    with _lock:
        c = _get_conn()
        c.execute("DELETE FROM sessions WHERE token=?", (token,))
        c.commit()


# ── 对话 ──
def create_conversation(user_id: int, title: str = "新对话") -> int:
    t = _now()
    with _lock:
        cur = _get_conn().execute(
            "INSERT INTO conversations (user_id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (user_id, title[:120] or "新对话", t, t),
        )
        _get_conn().commit()
        return int(cur.lastrowid)


def list_conversations(user_id: int) -> list[dict]:
    with _lock:
        c = _get_conn()
        rows = c.execute(
            "SELECT c.id, c.title, c.created_at, c.updated_at, "
            "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS msg_count "
            "FROM conversations c WHERE c.user_id=? ORDER BY c.updated_at DESC",
            (user_id,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "message_count": r["msg_count"],
        }
        for r in rows
    ]


def get_conversation(conv_id: int, user_id: int) -> Optional[dict]:
    with _lock:
        row = (
            _get_conn()
            .execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id))
            .fetchone()
        )
        return dict(row) if row else None


def rename_conversation(conv_id: int, user_id: int, title: str) -> bool:
    with _lock:
        c = _get_conn()
        n = c.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=? AND user_id=?",
            (title[:120] or "新对话", _now(), conv_id, user_id),
        ).rowcount
        c.commit()
        return n > 0


def delete_conversation(conv_id: int, user_id: int) -> bool:
    with _lock:
        c = _get_conn()
        n = c.execute(
            "DELETE FROM conversations WHERE id=? AND user_id=?", (conv_id, user_id)
        ).rowcount
        c.commit()
        return n > 0


def add_message(
    conv_id: int, role: str, content: str, tokens_prompt: int = 0, tokens_completion: int = 0
) -> int:
    t = _now()
    with _lock:
        c = _get_conn()
        cur = c.execute(
            "INSERT INTO messages (conversation_id, role, content, tokens_prompt, tokens_completion, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (conv_id, role, content, tokens_prompt, tokens_completion, t),
        )
        # 更新会话的 updated_at
        c.execute("UPDATE conversations SET updated_at=? WHERE id=?", (t, conv_id))
        c.commit()
        return int(cur.lastrowid)


def list_messages(conv_id: int, limit: int = 20) -> list[dict]:
    """按时间升序返回历史（用于拼到 LLM 上下文）。"""
    with _lock:
        rows = (
            _get_conn()
            .execute(
                "SELECT role, content, tokens_prompt, tokens_completion FROM messages "
                "WHERE conversation_id=? ORDER BY id ASC LIMIT ?",
                (conv_id, limit),
            )
            .fetchall()
        )
    return [
        {
            "role": r["role"],
            "content": r["content"],
            "tokens_prompt": r["tokens_prompt"],
            "tokens_completion": r["tokens_completion"],
        }
        for r in rows
    ]


# ── token 用量 ──
def add_token_usage(user_id: int, prompt_tokens: int, completion_tokens: int) -> None:
    if prompt_tokens <= 0 and completion_tokens <= 0:
        return
    with _lock:
        c = _get_conn()
        c.execute(
            "INSERT INTO token_ledger (user_id, prompt_tokens, completion_tokens, at) VALUES (?,?,?,?)",
            (user_id, max(0, prompt_tokens), max(0, completion_tokens), _now()),
        )
        c.commit()


def get_user_usage(user_id: int) -> dict:
    with _lock:
        c = _get_conn()
        row = c.execute(
            "SELECT COALESCE(SUM(prompt_tokens),0) AS p, "
            "COALESCE(SUM(completion_tokens),0) AS cc FROM token_ledger WHERE user_id=?",
            (user_id,),
        ).fetchone()
        user = c.execute(
            "SELECT created_at, last_active FROM users WHERE id=?", (user_id,)
        ).fetchone()
        conv_count = c.execute(
            "SELECT COUNT(*) FROM conversations WHERE user_id=?", (user_id,)
        ).fetchone()[0]
    return {
        "total_prompt_tokens": int(row["p"]),
        "total_completion_tokens": int(row["cc"]),
        "total_tokens": int(row["p"]) + int(row["cc"]),
        "conversations": conv_count,
        "created_at": user["created_at"] if user else 0,
        "last_active": user["last_active"] if user else 0,
    }


def get_user_by_username(username: str) -> Optional[dict]:
    with _lock:
        row = _get_conn().execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
        return _row_to_user(row) if row else None

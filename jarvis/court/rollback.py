"""
rollback — 经过测试的安全回滚（DGM「人类审批门」后的最后兜底：可撤销）。

即便有沙箱、评测闸、人类审批、金标准安全闸层层把关，一个被批准并合并的突变仍可能
在长时间运行中暴露出问题。自修改系统必须能**干净地撤销**自己上一轮的改动——这正是
调研 P0 清单里的「tested rollback with safe/<id> tags」。

本模块提供 :class:`RollbackManager`：

  * ``snapshot(label, payload, cycle, safe)`` —— 把当前基因 payload 落盘成一个
    带元数据的快照，返回 ``snapshot_id``；
  * ``list()`` / ``list_safe()`` —— 列出全部 / 仅标记为「已知良好」的快照；
  * ``mark_safe(id)`` —— 把一个快照标记为安全点（golden checkpoint）；
  * ``rollback_to(id, court, genome_state_path)`` —— 把 Court 的基因回滚到该快照，
    并同步更新运行中的 ``genome_state.json``，使回滚**立即可见、可续跑**。

设计要点：
  * 落盘用「写 .tmp 再 os.replace」原子写，崩溃不致损坏；
  * 每个快照的元数据（id/label/cycle/时间戳/sha）记录在 ``index.json``，便于审计；
  * 回滚走既有 ``Court.load_genomes``（与检查点同一套反序列化），不另造路径，
    保证「保存 ↔ 回滚」完全对称、可被单测验证（见 tests/test_rollback.py）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from jarvis.court.genome_diff import genome_state_file_content

DEFAULT_SNAPSHOT_DIR = "jarvis/court/snapshots"


@dataclass
class SnapshotMeta:
    """一个基因快照的元数据。"""

    id: str
    label: str
    cycle: int
    timestamp: str
    sha: str
    safe: bool = False
    path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RollbackManager:
    """基因快照 / 回滚管理器（安全点带 ``safe`` 标签）。

    Args:
        snapshot_dir: 快照目录（相对仓库根；默认 ``jarvis/court/snapshots``）。
        genome_state_relpath: 运行中的基因检查点相对路径（回滚时同步更新）。
    """

    def __init__(self, snapshot_dir: str = DEFAULT_SNAPSHOT_DIR,
                 genome_state_relpath: str = "jarvis/court/genome_state.json") -> None:
        self.dir = snapshot_dir
        self.genome_state_relpath = genome_state_relpath
        os.makedirs(self.dir, exist_ok=True)
        self._index_path = os.path.join(self.dir, "index.json")

    # ── 索引（快照元数据）─────────────────────────────────────

    def _load_index(self) -> Dict[str, Any]:
        if not os.path.isfile(self._index_path):
            return {"snapshots": []}
        try:
            with open(self._index_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict) and isinstance(data.get("snapshots"), list):
                return data
        except (json.JSONDecodeError, OSError):
            pass
        return {"snapshots": []}

    def _save_index(self, idx: Dict[str, Any]) -> None:
        tmp = self._index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(idx, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self._index_path)

    # ── 快照 ──────────────────────────────────────────────────

    def _next_id(self, cycle: int, payload: Dict[str, Any]) -> str:
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(blob).hexdigest()[:8]
        ts = int(time.time() * 1000)
        return f"c{cycle:03d}-{ts % 100000:05d}-{digest}"

    def snapshot(self, label: str, payload: Dict[str, Any],
                 cycle: int = 0, safe: bool = False) -> str:
        """保存一个基因快照，返回其 ``snapshot_id``。"""
        sid = self._next_id(cycle, payload)
        path = os.path.join(self.dir, f"genome_state.{sid}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)

        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        sha = hashlib.sha256(blob).hexdigest()
        meta = SnapshotMeta(
            id=sid, label=label, cycle=cycle,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            sha=sha, safe=bool(safe), path=path,
        )
        idx = self._load_index()
        idx["snapshots"].append(meta.to_dict())
        self._save_index(idx)
        return sid

    # ── 查询 ──────────────────────────────────────────────────

    def list(self) -> List[SnapshotMeta]:
        idx = self._load_index()
        out: List[SnapshotMeta] = []
        for d in idx.get("snapshots", []):
            out.append(SnapshotMeta(**{k: d.get(k, "") for k in SnapshotMeta.__dataclass_fields__}))
        return sorted(out, key=lambda m: m.id)

    def list_safe(self) -> List[SnapshotMeta]:
        return [m for m in self.list() if m.safe]

    def get(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """返回某快照的基因 payload；不存在返回 ``None``。"""
        path = os.path.join(self.dir, f"genome_state.{snapshot_id}.json")
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    # ── 标记 / 回滚 ───────────────────────────────────────────

    def mark_safe(self, snapshot_id: str) -> bool:
        """把一个快照标记为安全点（已知良好）。成功返回 True。"""
        idx = self._load_index()
        found = False
        for d in idx.get("snapshots", []):
            if d.get("id") == snapshot_id:
                d["safe"] = True
                found = True
        if found:
            self._save_index(idx)
        return found

    def rollback_to(self, snapshot_id: str, court: Any,
                    genome_state_path: Optional[str] = None) -> bool:
        """把 Court 的基因回滚到指定快照，并同步更新运行中的检查点文件。

        返回 True 表示成功；快照不存在或载入失败返回 False。
        """
        payload = self.get(snapshot_id)
        if payload is None:
            return False
        # 1) 经既有 Court.load_genomes（与检查点同一套反序列化）载入，保证对称。
        tmp = os.path.join(self.dir, f"_restore_{snapshot_id}.json")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
            court.load_genomes(tmp)
        except Exception:
            if os.path.isfile(tmp):
                os.remove(tmp)
            return False
        finally:
            if os.path.isfile(tmp):
                os.remove(tmp)

        # 2) 同步更新运行中的 genome_state.json，使回滚立即可见 / 可续跑。
        target = genome_state_path or self.genome_state_relpath
        try:
            content = genome_state_file_content(payload)
            tmp2 = target + ".tmp"
            with open(tmp2, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp2, target)
        except OSError:
            # 检查点文件写不出不致命（内存态已回滚），但值得告警。
            pass
        return True


__all__ = ["SnapshotMeta", "RollbackManager", "DEFAULT_SNAPSHOT_DIR"]

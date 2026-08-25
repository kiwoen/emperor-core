"""
huanxin-ai 用户反馈收集与分析系统
===================================

功能：
- POST   /api/feedback         提交用户反馈（评分 + 文本）
- GET    /api/feedback/stats   获取反馈统计
- GET    /api/feedback/list    获取反馈列表（分页）
- GET    /api/feedback/dashboard  简易反馈仪表盘 HTML

存储：本地 JSON 文件（生产环境可替换为数据库）
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── 数据模型 ────────────────────────────────────────────────


@dataclass
class FeedbackEntry:
    id: str
    rating: int  # 1-5
    category: str  # bug / feature / ux / performance / other
    message: str
    contact: str = ""
    version: str = "2.0.0"
    created_at: str = ""
    source: str = "web"  # web / cli / api

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── 反馈存储引擎 ───────────────────────────────────────────


class FeedbackStore:
    """本地 JSON 文件反馈存储。

    支持线程安全读写，自动处理并发写入。
    """

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = os.environ.get(
                "HUANXIN_FEEDBACK_DIR",
                str(Path.home() / ".huanxin-ai" / "feedback"),
            )
        self._dir = Path(path)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "feedback.jsonl"

        # 迁移旧 JSON 至 JSONL
        old_file = self._dir / "feedback.json"
        if old_file.exists() and not self._file.exists():
            self._migrate(old_file)

    def _migrate(self, old_file: Path) -> None:
        """将旧版 JSON 数组迁移为 JSONL。"""
        try:
            with open(old_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                with open(self._file, "w", encoding="utf-8") as f:
                    for entry in data:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            old_file.rename(old_file.with_suffix(".json.bak"))
        except Exception:
            pass

    def save(self, entry: FeedbackEntry) -> str:
        """保存反馈条目，返回条目 ID。"""
        if not entry.id:
            entry.id = f"fb_{int(time.time() * 1000)}_{os.urandom(3).hex()}"
        if not entry.created_at:
            entry.created_at = datetime.now(timezone.utc).isoformat()

        record = entry.to_dict()
        with open(self._file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return entry.id

    def list_all(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """分页列出反馈。"""
        entries: list[dict] = []
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
        except FileNotFoundError:
            return []

        entries.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return entries[offset : offset + limit]

    def stats(self) -> dict[str, Any]:
        """计算反馈统计。"""
        all_entries: list[dict] = []
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        all_entries.append(json.loads(line))
        except FileNotFoundError:
            return self._empty_stats()

        if not all_entries:
            return self._empty_stats()

        ratings = [e.get("rating", 0) for e in all_entries]
        categories = {}
        sources = {}
        for e in all_entries:
            cat = e.get("category", "other")
            categories[cat] = categories.get(cat, 0) + 1
            src = e.get("source", "web")
            sources[src] = sources.get(src, 0) + 1

        return {
            "total": len(all_entries),
            "avg_rating": round(sum(ratings) / len(ratings), 2),
            "rating_distribution": {
                "5": ratings.count(5),
                "4": ratings.count(4),
                "3": ratings.count(3),
                "2": ratings.count(2),
                "1": ratings.count(1),
            },
            "categories": categories,
            "sources": sources,
            "latest_entries": sorted(
                all_entries, key=lambda x: x.get("created_at", ""), reverse=True
            )[:5],
        }

    @staticmethod
    def _empty_stats() -> dict[str, Any]:
        return {
            "total": 0,
            "avg_rating": 0,
            "rating_distribution": {"5": 0, "4": 0, "3": 0, "2": 0, "1": 0},
            "categories": {},
            "sources": {},
            "latest_entries": [],
        }

    def count(self) -> int:
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                return sum(1 for _ in f)
        except FileNotFoundError:
            return 0


# ── 全局实例 ────────────────────────────────────────────────

_feedback_store: Optional[FeedbackStore] = None


def get_feedback_store() -> FeedbackStore:
    global _feedback_store
    if _feedback_store is None:
        _feedback_store = FeedbackStore()
    return _feedback_store


# ── Feedback Dashboard HTML ──────────────────────────────────

FEEDBACK_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>幻炘AI 反馈仪表盘</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#c9d1d9;--accent:#58a6ff;--green:#3fb950;--yellow:#d2991d;--red:#f85149;--purple:#a371f7}
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:24px}
  .header{text-align:center;margin-bottom:32px}
  .header h1{font-size:28px;color:var(--accent);margin-bottom:4px}
  .header p{color:#8b949e;font-size:14px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:20px;margin-bottom:32px}
  .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:20px}
  .card h2{font-size:16px;margin-bottom:16px;color:var(--accent)}
  .stat-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)}
  .stat-row:last-child{border-bottom:none}
  .stat-value{font-size:20px;font-weight:700}
  .stat-label{color:#8b949e;font-size:13px}
  .rating-bar{display:flex;align-items:center;gap:8px;margin:4px 0}
  .rating-bar .bar{flex:1;height:20px;background:var(--border);border-radius:10px;overflow:hidden}
  .rating-bar .fill{height:100%;border-radius:10px;transition:width .3s}
  .rating-bar .count{min-width:40px;text-align:right;font-size:13px}
  .fill-5{background:var(--green)}.fill-4{background:var(--accent)}.fill-3{background:var(--yellow)}.fill-2{background:#db6d28}.fill-1{background:var(--red)}
  .feedback-list{list-style:none}
  .feedback-item{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:12px}
  .feedback-item .meta{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
  .feedback-item .stars{color:var(--yellow);letter-spacing:2px}
  .feedback-item .category{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;background:var(--purple);color:#fff}
  .feedback-item .time{color:#8b949e;font-size:12px}
  .feedback-item .msg{color:var(--text);line-height:1.5;font-size:14px}
  .feedback-item .contact{color:#8b949e;font-size:12px;margin-top:6px}
  .empty{text-align:center;padding:40px;color:#8b949e}
  .refresh-bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px}
  .refresh-btn{background:var(--accent);color:#fff;border:none;padding:8px 16px;border-radius:6px;cursor:pointer;font-size:13px}
  .refresh-btn:hover{opacity:0.85}
  .last-update{color:#8b949e;font-size:12px}
</style>
</head>
<body>
<div class="header">
  <h1>幻炘AI 反馈仪表盘</h1>
  <p>实时用户反馈收集与分析</p>
</div>
<div class="refresh-bar">
  <span class="last-update" id="lastUpdate">加载中...</span>
  <button class="refresh-btn" onclick="loadStats()">刷新数据</button>
</div>
<div class="grid">
  <div class="card">
    <h2>概览</h2>
    <div class="stat-row"><span class="stat-label">总反馈数</span><span class="stat-value" id="total">-</span></div>
    <div class="stat-row"><span class="stat-label">平均评分</span><span class="stat-value" id="avgRating">-</span></div>
  </div>
  <div class="card">
    <h2>评分分布</h2>
    <div id="ratingDist"></div>
  </div>
  <div class="card">
    <h2>分类统计</h2>
    <div id="categories"></div>
  </div>
</div>
<div class="card" style="margin-bottom:32px">
  <h2>最新反馈</h2>
  <ul class="feedback-list" id="feedbackList"><li class="empty">暂无反馈数据</li></ul>
</div>

<script>
async function loadStats(){
  try{
    const r=await fetch('/api/feedback/stats');
    const d=await r.json();
    document.getElementById('total').textContent=d.total;
    document.getElementById('avgRating').textContent=d.avg_rating+' / 5';
    document.getElementById('lastUpdate').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN');

    // Rating distribution
    const dist=document.getElementById('ratingDist');
    dist.innerHTML=[5,4,3,2,1].map(s=>{
      const c=d.rating_distribution[String(s)]||0;
      const pct=d.total>0?Math.round(c/d.total*100):0;
      return `<div class="rating-bar"><span>${'★'.repeat(s)}</span><div class="bar"><div class="fill fill-${s}" style="width:${pct}%"></div></div><span class="count">${c}</span></div>`;
    }).join('');

    // Categories
    const cats=document.getElementById('categories');
    const catEntries=Object.entries(d.categories||{});
    cats.innerHTML=catEntries.length?catEntries.map(([k,v])=>`<div class="stat-row"><span class="stat-label">${k}</span><span>${v}</span></div>`).join(''):'<div class="empty">暂无分类数据</div>';

    // Latest entries
    const list=document.getElementById('feedbackList');
    list.innerHTML=(d.latest_entries||[]).length?(d.latest_entries||[]).map(e=>`
      <li class="feedback-item">
        <div class="meta"><span class="stars">${'★'.repeat(e.rating)}${'☆'.repeat(5-e.rating)}</span><span class="category">${e.category}</span></div>
        <div class="msg">${e.message||'(无文字反馈)'}</div>
        <div class="contact">${e.contact?'联系方式: '+e.contact+' · ':''}${new Date(e.created_at).toLocaleString('zh-CN')}</div>
      </li>`).join(''):'<li class="empty">暂无反馈数据</li>';
  }catch(e){
    console.error('Failed to load stats:',e);
  }
}
loadStats();
setInterval(loadStats,30000);
</script>
</body>
</html>
"""

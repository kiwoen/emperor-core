"""Phase 9 基因快照/回滚测试。"""

from __future__ import annotations

import json
import os

from jarvis.court.court import Court, CourtConfig
from jarvis.court.rollback import RollbackManager
from jarvis.court.genome_diff import genome_state_file_content
from jarvis.self_evolve import default_ministers


def _fresh_court():
    c = Court(CourtConfig())
    c.register_many(default_ministers())
    return c


def test_snapshot_list_and_get(tmp_path):
    mgr = RollbackManager(snapshot_dir=str(tmp_path))
    p1 = _fresh_court().genome_state_payload()
    sid = mgr.snapshot("c1", p1, cycle=1, safe=True)
    assert sid
    rows = mgr.list()
    assert len(rows) == 1
    assert rows[0].safe is True
    assert mgr.get(sid) == p1


def test_mark_safe_and_list_safe(tmp_path):
    mgr = RollbackManager(snapshot_dir=str(tmp_path))
    p1 = _fresh_court().genome_state_payload()
    sid = mgr.snapshot("c1", p1, cycle=1, safe=False)
    assert mgr.list_safe() == []
    assert mgr.mark_safe(sid) is True
    assert len(mgr.list_safe()) == 1


def test_rollback_to_restores_genomes(tmp_path):
    court = _fresh_court()
    before = court.genome_state_payload()
    before_names = {g["name"] for g in before["genomes"]}

    mgr = RollbackManager(snapshot_dir=str(tmp_path))
    sid = mgr.snapshot("base", before, cycle=0, safe=True)

    # 制造「坏突变」：加一个大臣，使基因集合与基线不同
    court.register_many([{
        "name": "intruder", "domain": "misc", "temperature": 0.5,
        "confidence_baseline": 0.5,
    }])
    assert {g["name"] for g in court.genome_state_payload()["genomes"]} != before_names

    genome_file = str(tmp_path / "genome_state.json")
    ok = mgr.rollback_to(sid, court, genome_file)
    assert ok is True

    after_names = {g["name"] for g in court.genome_state_payload()["genomes"]}
    assert after_names == before_names

    # 运行中的检查点文件也应被同步为基线内容
    with open(genome_file, "r", encoding="utf-8") as fh:
        written = json.load(fh)
    assert {g["name"] for g in written["genomes"]} == before_names


def test_rollback_to_unknown_returns_false(tmp_path):
    mgr = RollbackManager(snapshot_dir=str(tmp_path))
    court = _fresh_court()
    assert mgr.rollback_to("nope", court) is False

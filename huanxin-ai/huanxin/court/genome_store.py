"""Genome persistence layer — save/load MinisterGenome objects to/from disk.

Evolution progress (generations, crossovers, mutations) produces valuable
genetic material. This module ensures that material survives restarts by
serialising genomes to a JSON file.

Design decisions:
- JSON (not pickle): human-readable, debuggable, version-tolerant
- Atomic write: write to .tmp then rename, preventing corruption on crash
- Roundtrip fidelity: all MinisterGenome fields are primitive types
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from huanxin.court.evolution import MinisterGenome


class GenomeStore:
    """Serialises and deserialises MinisterGenome objects as JSON."""

    @staticmethod
    def to_dict(genome: "MinisterGenome") -> dict:
        """Convert a single genome to a plain dict."""
        return {
            "name": genome.name,
            "domain": genome.domain,
            "temperature": genome.temperature,
            "confidence_baseline": genome.confidence_baseline,
            "exploration_rate": genome.exploration_rate,
            "conservatism": genome.conservatism,
            "prompt_mutation_rate": genome.prompt_mutation_rate,
            "specialization_weight": genome.specialization_weight,
            "generation": genome.generation,
            "parent": genome.parent,
        }

    @staticmethod
    def from_dict(data: dict) -> "MinisterGenome":
        """Reconstruct a genome from a plain dict."""
        from huanxin.court.evolution import MinisterGenome
        return MinisterGenome(
            name=data["name"],
            domain=data["domain"],
            temperature=data.get("temperature", 0.7),
            confidence_baseline=data.get("confidence_baseline", 0.85),
            exploration_rate=data.get("exploration_rate", 0.3),
            conservatism=data.get("conservatism", 0.5),
            prompt_mutation_rate=data.get("prompt_mutation_rate", 0.1),
            specialization_weight=data.get("specialization_weight", 1.0),
            generation=data.get("generation", 0),
            parent=data.get("parent", ""),
        )

    @staticmethod
    def save(
        path: str | Path,
        genomes: list["MinisterGenome"],
        metadata: dict | None = None,
    ) -> None:
        """Atomically write genomes to a JSON file.

        Metadata (active_count, shadow_count, cycle, etc.) is included
        alongside the genome array for quick inspection.

        持久化加固（针对并发进化循环 + 命名卷竞态）：
        - 每次写入使用 tempfile.mkstemp 生成的【唯一】临时文件名，避免多个
          进化循环并发调用 save_genomes() 时共享同一个 "genomes.json.tmp"、
          互相把对方的临时文件 os.replace 掉（原 bug：一方在 replace 前发现
          共享 tmp 已消失而抛 RuntimeError）。
        - flush + fsync 确保数据真正落盘，而非停留在页缓存。
        - os.replace 在同一文件系统内是原子的；后写者胜出即可，因为基因组每轮
          都会重新计算，last-writer-wins 语义正确。
        - 若 os.replace 抛 FileNotFoundError（临时文件在瞬时竞态中消失），重新
          生成唯一临时文件并重试一次；任何失败路径都清理残留临时文件。
        - path.parent 不可写时，best-effort 用 os.chmod 放宽权限（忽略 OSError）。
        """
        path = Path(path)

        # Best-effort relax parent dir perms so the non-root runtime user can
        # write. Named volumes occasionally expose restrictive modes right after
        # a cold start.
        try:
            if path.parent.exists():
                os.chmod(path.parent, 0o755)
        except OSError:
            pass

        path.parent.mkdir(parents=True, exist_ok=True)

        payload: dict = {
            "version": 1,
            "metadata": metadata or {},
            "genomes": [GenomeStore.to_dict(g) for g in genomes],
        }

        def _attempt() -> None:
            """写出一个全新的唯一临时文件，再原子替换目标。

            任意失败都会删除该临时文件（可能写一半）后原样抛出，由调用方决定
            是否重试。每次调用都生成独立的临时文件名，故并发调用互不干扰。
            """
            fd, tmp_name = tempfile.mkstemp(
                suffix=".tmp", prefix=f"{path.stem}.", dir=str(path.parent)
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_name, str(path))  # atomic on same filesystem
            except BaseException:
                # 失败：清理（可能不完整的）临时文件后重新抛出。
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise

        try:
            _attempt()
        except FileNotFoundError:
            # 唯一临时文件在瞬时竞态中消失——重新生成一个全新的临时文件并
            # 恰好重试一次。
            try:
                _attempt()
            except FileNotFoundError:
                raise RuntimeError(
                    f"os.replace 重试后仍找不到源文件，目标：{path}"
                )

    @staticmethod
    def load(path: str | Path) -> tuple[list["MinisterGenome"], dict]:
        """Load genomes from a JSON file.

        Returns (genomes, metadata). Returns ([], {}) if file doesn't
        exist or is corrupt.
        """
        path = Path(path)
        if not path.is_file():
            return [], {}

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            return [], {}

        genomes = [
            GenomeStore.from_dict(d)
            for d in payload.get("genomes", [])
        ]
        metadata = payload.get("metadata", {})
        return genomes, metadata

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

        持久化加固（针对 Docker 命名卷冷启动竞态）：
        - 写完临时文件后 flush + fsync，确保数据真正落盘，而非停留在页缓存。
        - os.replace 之前校验临时文件是否存在；若已丢失，抛出含路径的 RuntimeError。
        - os.replace 若抛 FileNotFoundError（overlayfs / 命名卷瞬时可见性窗口导致
          源文件在写入与替换之间"消失"），重试一次：重新落盘临时文件再 replace。
        - 任何失败路径都清理残留的临时文件，避免下次落盘被污染。
        - 保留原子写语义：先写 .tmp 再 replace，替换在同一文件系统内是原子的。
        - path.parent 不可写时，best-effort 用 os.chmod 放宽权限（忽略 OSError）。
        """
        path = Path(path)

        # Defensive: relax the parent directory's permissions best-effort so we
        # can write into it. Named volumes occasionally expose restrictive modes
        # immediately after a cold start, before they are fully visible to the
        # non-root runtime user.
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

        # Use a suffix like "genomes.json.tmp" so the temp file is far less
        # likely to collide with other ".tmp" artefacts in the same directory.
        tmp_path = path.with_suffix(path.suffix + ".tmp")

        def _write_tmp() -> None:
            """写出 payload 到临时文件，并保证落盘（flush + fsync）。"""
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

        def _cleanup_tmp() -> None:
            """Best-effort 删除残留临时文件，忽略一切错误。"""
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass

        # --- 第一次尝试：写出临时文件并执行原子替换 ---
        _write_tmp()
        try:
            # 替换前确认源（临时文件）仍然存在。
            if not tmp_path.exists():
                _cleanup_tmp()
                raise RuntimeError(
                    f"临时文件在 os.replace 之前丢失：{tmp_path} "
                    f"（目标路径：{path}）。疑似命名卷冷启动竞态。"
                )
            os.replace(tmp_path, path)  # atomic on same filesystem
            return
        except FileNotFoundError:
            # 临时文件在"写出之后、替换之前"的窗口内消失——这正是 overlayfs /
            # 命名卷冷启动竞态的典型表现。清理后重试一次。
            _cleanup_tmp()
            _write_tmp()
            try:
                os.replace(tmp_path, path)
                return
            except FileNotFoundError:
                _cleanup_tmp()
                raise RuntimeError(
                    f"os.replace 重试后仍找不到源文件：{tmp_path} -> {path}"
                )
        except Exception:
            # 其它任何异常：清理临时文件后原样向上抛出。
            _cleanup_tmp()
            raise

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

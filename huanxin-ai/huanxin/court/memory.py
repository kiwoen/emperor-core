"""
CourtMemory (朝堂记忆系统) — persistent multi-minister experience memory.

Gives every minister long-term memory across court sessions. Records every
decree outcome and uses it to:
  1. Boost routing scores for ministers who succeeded on similar tasks
  2. Provide context-rich summaries to ministers before each new task
  3. Share successful patterns across ministers with similar domains
  4. Decay old memories so the system adapts to shifting task patterns

Phase 8 of the CourtOrchestrator pipeline:
  Phase 7: Evolution
  Phase 8: Memory — record memorial outcomes, decay old entries

Design:
  - Keyword-based similarity (fast, no embedding dependency)
  - Exponential time decay (older memories matter less)
  - Domain + keyword overlap for relevance scoring
  - Cap on total entries to bound memory usage
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger("huanxin.court.memory")


# ------------------------------------------------------------------
# Data Structures
# ------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """A single recorded experience from a court session.

    Attributes:
        id:             Unique hash of (minister + edict_id) for dedup
        domain:         Task domain (engineering, security, research, etc.)
        minister_name:  Which minister handled it
        intent:         The original user intent text (used for similarity)
        intent_keywords: Tokenized keywords for fast overlap computation
        success:        Whether the minister succeeded
        confidence:     Minister's confidence at the time
        execution_time_ms: How long it took
        timestamp:      Unix timestamp of recording
        merit:          Merit awarded (from merit board, 0 if not available)
        weight:         Decay weight, starts at 1.0 and decays over time
        tags:           Optional tags for categorization
    """

    id: str
    domain: str
    minister_name: str
    intent: str
    intent_keywords: list[str]
    success: bool
    confidence: float
    execution_time_ms: float
    timestamp: float
    merit: float = 0.0
    weight: float = 1.0
    tags: list[str] = field(default_factory=list)


@dataclass
class MemorySummary:
    """Aggregate knowledge for a domain, usable as minister context.

    Attributes:
        domain:             The domain
        total_entries:      Total memories in this domain
        success_rate:       Overall success rate
        avg_confidence:     Average calibrated confidence
        avg_execution_ms:   Average execution time
        recent_successes:   Number of successes in last 10 entries
        top_minister:       Best performing minister name
        top_patterns:       Most successful intent keyword patterns
    """

    domain: str
    total_entries: int
    success_rate: float
    avg_confidence: float
    avg_execution_ms: float
    recent_successes: int
    top_minister: str
    top_patterns: list[str]


@dataclass
class QueryResult:
    """Result of a memory similarity query.

    Attributes:
        entry:      The memory entry
        relevance:  Similarity score (0.0-1.0) based on keyword overlap
    """

    entry: MemoryEntry
    relevance: float


# ------------------------------------------------------------------
# CourtMemory
# ------------------------------------------------------------------


class CourtMemory:
    """Centralized memory for all ministers across all domains.

    Usage:
        memory = CourtMemory(decay_factor=0.95, max_entries=500)

        # After each decree:
        memory.record(MemoryEntry(...))

        # Before routing:
        context = memory.summarize_context("chancellor", "engineering", intent)
        # → "Past similar tasks: 3/4 succeeded. Pattern: 'code review' → high success"

        # Query similar experiences:
        results = memory.query("engineering", "代码安全漏洞分析", top_k=5)

        # Periodic decay:
        memory.apply_decay()

        # Cross-minister knowledge sharing:
        memory.propagate_knowledge("chancellor", "security")
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        decay_factor: float = 0.95,
        max_entries: int = 500,
        similarity_threshold: float = 0.15,
        decay_interval_hours: float = 1.0,
        max_per_group: Optional[int] = None,
    ) -> None:
        """Initialize court memory.

        Args:
            decay_factor:           Multiplicative decay per decay_interval (0-1)
            max_entries:            Hard cap on total stored entries
            similarity_threshold:   Minimum keyword overlap ratio for a match
            decay_interval_hours:   How often decay is applied (in hours)
            max_per_group:          Optional per-(domain, minister) retention cap.
                                    When set, ``record`` keeps only the most
                                    recent ``max_per_group`` entries per group and
                                    drops the oldest — bounding staleness so old
                                    samples never permanently dominate routing /
                                    warm-start. ``None`` = no per-group cap
                                    (backward compatible, default).
        """
        self._entries: list[MemoryEntry] = []
        self.decay_factor = max(0.5, min(1.0, decay_factor))
        self.max_entries = max_entries
        self.similarity_threshold = similarity_threshold
        self.decay_interval_s = decay_interval_hours * 3600.0
        self._last_decay: float = time.time()
        self.max_per_group = max_per_group

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def entry_count(self) -> int:
        """Total number of stored entries."""
        return len(self._entries)

    @property
    def domains(self) -> list[str]:
        """All unique domains with recorded memories."""
        return sorted({e.domain for e in self._entries})

    def get_entries(self) -> list[MemoryEntry]:
        """Return a shallow copy of all entries (for testing/export)."""
        return list(self._entries)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, entry: MemoryEntry) -> str:
        """Record a memory entry. Returns the entry id.

        Idempotent: if an entry with the same id already exists, it is
        skipped and the existing id is returned.
        """
        # Dedup check
        for existing in self._entries:
            if existing.id == entry.id:
                logger.debug("Skipping duplicate entry %s", entry.id)
                return entry.id

        self._entries.append(entry)

        # Enforce per-group retention window (bounds staleness at the source)
        if self.max_per_group:
            self.prune_oldest_per_group()

        # Enforce global max cap
        removed = self.prune_oldest()
        if removed:
            logger.info("Pruned %d oldest entries (cap=%d)", removed, self.max_entries)

        # Auto-decay on record
        self._maybe_decay()

        logger.info(
            "Recorded memory: %s | %s | %s | success=%s",
            entry.minister_name, entry.domain, entry.intent[:40], entry.success,
        )
        return entry.id

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query(
        self,
        domain: str,
        intent: str,
        top_k: int = 5,
        success_only: bool = False,
    ) -> list[QueryResult]:
        """Find most relevant past experiences for a domain + intent.

        Relevance is computed from keyword overlap between the intent
        and stored intent_keywords, scaled by entry weight (decay).

        Args:
            domain:         Target domain
            intent:         Natural language intent string
            top_k:          Max results to return
            success_only:   If True, only return successful entries

        Returns:
            Sorted list of QueryResult (descending by relevance)
        """
        query_keywords = self._tokenize(intent)
        if not query_keywords:
            return []

        candidates: list[QueryResult] = []
        for entry in self._entries:
            if entry.domain != domain:
                continue
            if success_only and not entry.success:
                continue

            relevance = self._compute_relevance(query_keywords, entry)
            if relevance >= self.similarity_threshold:
                candidates.append(QueryResult(entry=entry, relevance=relevance))

        # Sort by (relevance * weight) descending
        candidates.sort(key=lambda r: r.relevance * r.entry.weight, reverse=True)
        return candidates[:top_k]

    def query_by_minister(
        self,
        minister_name: str,
        top_k: int = 10,
    ) -> list[MemoryEntry]:
        """Get most recent entries for a specific minister."""
        matches = sorted(
            [e for e in self._entries if e.minister_name == minister_name],
            key=lambda e: e.timestamp,
            reverse=True,
        )
        return matches[:top_k]

    # ------------------------------------------------------------------
    # Domain Knowledge
    # ------------------------------------------------------------------

    def get_domain_stats(self, domain: str) -> Optional[MemorySummary]:
        """Get aggregate statistics for a domain."""
        entries = [e for e in self._entries if e.domain == domain]
        if not entries:
            return None

        total = len(entries)
        successes = sum(1 for e in entries if e.success)
        avg_conf = sum(e.confidence for e in entries) / total
        avg_time = sum(e.execution_time_ms for e in entries) / total
        recent_10 = entries[-10:] if total >= 10 else entries
        recent_successes = sum(1 for e in recent_10 if e.success)

        # Top minister by success count
        minister_wins: dict[str, int] = {}
        for e in entries:
            if e.success:
                minister_wins[e.minister_name] = minister_wins.get(e.minister_name, 0) + 1
        top_minister = max(minister_wins, key=minister_wins.get) if minister_wins else "nobody"

        # Top patterns: most successful keyword patterns
        pattern_success: dict[str, tuple[int, int]] = {}  # pattern → (success, total)
        for e in entries:
            for kw in e.intent_keywords[:3]:
                s, t = pattern_success.get(kw, (0, 0))
                if e.success:
                    s += 1
                pattern_success[kw] = (s, t + 1)

        top_patterns = sorted(
            [(k, s, t) for k, (s, t) in pattern_success.items() if t >= 2],
            key=lambda x: x[1] / x[2],
            reverse=True,
        )[:5]
        top_pattern_strs = [f"{k}({s}/{t})" for k, s, t in top_patterns]

        return MemorySummary(
            domain=domain,
            total_entries=total,
            success_rate=successes / total,
            avg_confidence=avg_conf,
            avg_execution_ms=avg_time,
            recent_successes=recent_successes,
            top_minister=top_minister,
            top_patterns=top_pattern_strs,
        )

    def get_all_domain_stats(self) -> list[MemorySummary]:
        """Get stats for all domains."""
        summaries = []
        for domain in self.domains:
            s = self.get_domain_stats(domain)
            if s is not None:
                summaries.append(s)
        return summaries

    # ------------------------------------------------------------------
    # Context Generation (for ministers)
    # ------------------------------------------------------------------

    def summarize_context(
        self,
        minister_name: str,
        domain: str,
        intent: str,
    ) -> str:
        """Generate a concise memory context string for a minister.

        The minister can use this as a prompt prefix to improve performance
        on tasks similar to past experiences.

        Example output:
            "[Memory] Domain engineering: 12/15 succeeded recently.
             Similar tasks: 'code review' → succeeded ×3, 'debug crash' → succeeded ×1.
             Your success rate: 80%. Keep it up!"
        """
        parts: list[str] = []

        # Similar tasks
        similar = self.query(domain=domain, intent=intent, top_k=3)
        if similar:
            parts.append("Similar past tasks:")
            for r in similar:
                icon = "+" if r.entry.success else "-"
                intent_short = r.entry.intent[:40]
                parts.append(f"  [{icon}] \"{intent_short}\" (sim={r.relevance:.0%})")

        # Domain stats
        stats = self.get_domain_stats(domain)
        if stats:
            parts.append(
                f"Domain '{domain}': {stats.success_rate:.0%} success "
                f"({stats.total_entries} tasks), best performer: {stats.top_minister}"
            )

        # Minister's own stats
        own = [e for e in self._entries if e.minister_name == minister_name]
        if own:
            own_success = sum(1 for e in own if e.success)
            own_rate = own_success / len(own)
            parts.append(
                f"Your record: {own_success}/{len(own)} ({own_rate:.0%} success)"
            )

            if own_rate >= 0.8 and len(own) >= 5:
                parts.append("You are on a streak—keep the quality high!")
            elif own_rate < 0.3 and len(own) >= 3:
                parts.append("Consider being more cautious in confidence estimates.")

        if not parts:
            return "[Memory] No past experience for this domain."

        return "[Memory] " + " | ".join(parts)

    # ------------------------------------------------------------------
    # Knowledge Propagation
    # ------------------------------------------------------------------

    def propagate_knowledge(
        self,
        source_minister: str,
        target_domain: str,
        top_k: int = 5,
    ) -> list[MemoryEntry]:
        """Share successful patterns from a minister to another domain.

        Finds the minister's most successful patterns and creates synthetic
        light-weight memory entries in the target domain, so other ministers
        can benefit from cross-domain knowledge.

        Returns the list of propagated entries.
        """
        source_entries = [
            e for e in self._entries
            if e.minister_name == source_minister and e.success
        ]
        source_entries.sort(key=lambda e: e.confidence * e.weight, reverse=True)

        propagated: list[MemoryEntry] = []
        for entry in source_entries[:top_k]:
            # Create a propagated entry with reduced weight
            prop = MemoryEntry(
                id=f"propagate:{source_minister}→{target_domain}:{entry.id}",
                domain=target_domain,
                minister_name="__propagated__",
                intent=f"[From {source_minister}] {entry.intent}",
                intent_keywords=list(entry.intent_keywords),
                success=True,
                confidence=entry.confidence * 0.85,  # Reduced confidence
                execution_time_ms=entry.execution_time_ms,
                timestamp=time.time(),
                merit=entry.merit * 0.5,
                weight=entry.weight * 0.7,  # Lower weight for propagated
                tags=entry.tags + ["propagated"],
            )
            # Dedup check
            if any(e.id == prop.id for e in self._entries):
                continue
            self._entries.append(prop)
            propagated.append(prop)

        if propagated:
            logger.info(
                "Propagated %d entries from %s → domain=%s",
                len(propagated), source_minister, target_domain,
            )

        return propagated

    # ------------------------------------------------------------------
    # Decay & Maintenance
    # ------------------------------------------------------------------

    def apply_decay(self) -> int:
        """Apply exponential decay to all entries. Returns count of removed.

        Entries whose weight drops below 0.05 (effectively forgotten) are
        removed to free memory.
        """
        now = time.time()
        elapsed_hours = (now - self._last_decay) / 3600.0
        intervals = max(1, int(elapsed_hours / (self.decay_interval_s / 3600.0)))

        decay_multiplier = self.decay_factor ** intervals

        new_entries: list[MemoryEntry] = []
        removed = 0
        for entry in self._entries:
            entry.weight *= decay_multiplier
            if entry.weight >= 0.05:
                new_entries.append(entry)
            else:
                removed += 1

        self._entries = new_entries
        self._last_decay = now

        if removed:
            logger.info("Decay removed %d entries (weight < 0.05)", removed)
        return removed

    def prune_oldest(self) -> int:
        """Remove oldest entries if over max_entries. Returns count removed."""
        if len(self._entries) <= self.max_entries:
            return 0

        excess = len(self._entries) - self.max_entries
        # Sort by (weight * timestamp factor): keep most relevant
        self._entries.sort(key=lambda e: e.weight * (e.timestamp / 1e9), reverse=True)
        removed = self._entries[self.max_entries:]
        self._entries = self._entries[:self.max_entries]

        return len(removed)

    def prune_oldest_per_group(self) -> int:
        """Enforce ``max_per_group`` cap per (domain, minister_name) group.

        Keeps only the most recent ``max_per_group`` entries in each group and
        drops the oldest. Returns total count removed. No-op when
        ``max_per_group`` is ``None`` (default, backward compatible).
        """
        if not self.max_per_group or self.max_per_group <= 0:
            return 0

        by_group: dict = {}
        for e in self._entries:
            by_group.setdefault((e.domain, e.minister_name), []).append(e)

        keep: list[MemoryEntry] = []
        removed = 0
        for group_entries in by_group.values():
            if len(group_entries) <= self.max_per_group:
                keep.extend(group_entries)
                continue
            # 按时间排序，保留最新的 max_per_group 条，丢弃最旧的。
            ordered = sorted(group_entries, key=lambda e: e.timestamp)
            keep.extend(ordered[-self.max_per_group:])
            removed += len(ordered) - self.max_per_group

        if removed:
            self._entries = keep
            logger.info("Pruned %d stale per-group entries (max_per_group=%d)",
                        removed, self.max_per_group)
        return removed

    def per_minister_domain_quality(
        self,
        recency_decay: float = 1.0,
    ) -> dict:
        """Aggregate (success, total) per (minister, domain) for routing / warm-start.

        Args:
            recency_decay:  Weight of the *oldest* entry relative to the newest.
                            ``1.0`` → equal weighting (matches the original
                            unweighted behaviour, zero regression). ``0<d<1`` →
                            newer samples weigh more (old experience gradually
                            loses influence), so stale successes/failures do not
                            permanently dominate dispatch decisions. Weights are
                            computed deterministically from **insertion order**
                            (newest=1.0, oldest=d**(n-1)), independent of
                            wall-clock time, so it is reproducible in short
                            offline runs.

        Returns:
            ``{(minister, domain): (weighted_success, weighted_total)}``
        """
        if recency_decay >= 1.0:
            # Fast path: simple counts (identical to legacy aggregation).
            agg: dict = {}
            for e in self._entries:
                k = (e.minister_name, e.domain)
                s, t = agg.get(k, (0.0, 0.0))
                agg[k] = (s + (1.0 if e.success else 0.0), t + 1.0)
            return agg

        d = max(0.0, min(0.999, recency_decay))
        agg: dict = {}
        # 按 (minister, domain) 分组，组内按时间升序，最新权重=1.0。
        by_group: dict = {}
        for e in self._entries:
            by_group.setdefault((e.minister_name, e.domain), []).append(e)
        for k, entries in by_group.items():
            ordered = sorted(entries, key=lambda e: e.timestamp)
            n = len(ordered)
            w_success = 0.0
            w_total = 0.0
            for i, e in enumerate(ordered):
                w = d ** (n - 1 - i)  # 最旧 d^(n-1)，最新 1.0
                w_total += w
                if e.success:
                    w_success += w
            agg[k] = (w_success, w_total)
        return agg

    def clear_domain(self, domain: str) -> int:
        """Remove all entries for a domain. Returns count removed."""
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.domain != domain]
        return before - len(self._entries)

    def clear_all(self) -> int:
        """Remove all entries. Returns count removed."""
        count = len(self._entries)
        self._entries.clear()
        return count

    # ------------------------------------------------------------------
    # Persistence (让自我学习跨重启累积)
    # ------------------------------------------------------------------

    def save(self, path: str) -> str:
        """把全部记忆条目持久化到 JSON（原子写 tmp→replace，防半截文件）。

        这是「自我学习」能跨进程/跨重启累积的关键——没有持久化，每次运行
        学到的经验都会丢失。
        """
        payload = {
            "version": 1,
            "saved_at": time.time(),
            "decay_factor": self.decay_factor,
            "max_entries": self.max_entries,
            "max_per_group": self.max_per_group,
            "entries": [asdict(e) for e in self._entries],
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        logger.info("CourtMemory 已持久化 %d 条记忆 → %s", len(self._entries), path)
        return path

    @classmethod
    def load(cls, path: str) -> "CourtMemory":
        """从 JSON 恢复记忆；文件不存在或损坏时安全返回空记忆（绝不阻断运行）。"""
        mem = cls()
        if not os.path.exists(path):
            return mem
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            for d in payload.get("entries", []) or []:
                mem._entries.append(MemoryEntry(**d))
            # 恢复留存窗口配置，使 --resume 复用同一上限（不丢边界）。
            if payload.get("max_per_group") is not None:
                mem.max_per_group = int(payload["max_per_group"])
            logger.info("CourtMemory 已恢复 %d 条记忆 ← %s", len(mem._entries), path)
        except Exception:
            logger.warning("CourtMemory 恢复失败（已忽略，从空记忆开始）：%s", path, exc_info=True)
        return mem

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _maybe_decay(self) -> None:
        """Auto-apply decay if enough time has passed."""
        now = time.time()
        if now - self._last_decay >= self.decay_interval_s:
            self.apply_decay()

    def _compute_relevance(
        self, query_keywords: list[str], entry: MemoryEntry
    ) -> float:
        """Compute relevance score between query keywords and entry.

        Uses Jaccard-like overlap: |query ∩ entry_keywords| / max(|query|, |entry_keywords|)
        """
        if not query_keywords or not entry.intent_keywords:
            return 0.0

        q_set = set(query_keywords)
        e_set = set(entry.intent_keywords)
        intersection = q_set & e_set
        denominator = max(len(q_set), len(e_set))
        if denominator == 0:
            return 0.0

        return len(intersection) / denominator

    # ------------------------------------------------------------------
    # Static Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Extract meaningful keywords from intent text.

        English: split by spaces, keep words >= 3 chars.
        Chinese: use character bigrams + single chars >= 2 chars.
        Mixed: do both.
        """
        if not text:
            return []

        tokens: list[str] = []

        # English word extraction (>= 3 chars)
        import re
        en_words = re.findall(r"[a-zA-Z]{3,}", text)
        tokens.extend(w.lower() for w in en_words)

        # Chinese: character bigrams
        chinese = re.findall(r"[\u4e00-\u9fff]+", text)
        for chunk in chinese:
            if len(chunk) >= 4:
                # Use bigrams
                for i in range(len(chunk) - 1):
                    tokens.append(chunk[i:i + 2])
            elif len(chunk) >= 2:
                tokens.append(chunk)
            else:
                tokens.append(chunk)

        # Deduplicate while preserving order
        seen: set[str] = set()
        result: list[str] = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                result.append(t)
        return result

    @staticmethod
    def make_entry_id(minister_name: str, edict_id: str) -> str:
        """Generate a deterministic entry id from minister + edict_id."""
        raw = f"{minister_name}::{edict_id}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ------------------------------------------------------------------
# Convenience: build MemoryEntry from court session data
# ------------------------------------------------------------------


def memory_from_memorial(
    minister_name: str,
    edict_id: str,
    domain: str,
    intent: str,
    success: bool,
    confidence: float,
    execution_time_ms: float,
    merit: float = 0.0,
    tags: Optional[list[str]] = None,
) -> MemoryEntry:
    """Factory: build a MemoryEntry from a memorial's key fields."""
    entry_id = CourtMemory.make_entry_id(minister_name, edict_id)
    keywords = CourtMemory._tokenize(intent)

    return MemoryEntry(
        id=entry_id,
        domain=domain,
        minister_name=minister_name,
        intent=intent[:200],  # Truncate very long intents
        intent_keywords=keywords,
        success=success,
        confidence=confidence,
        execution_time_ms=execution_time_ms,
        timestamp=time.time(),
        merit=merit,
        tags=tags or [],
    )

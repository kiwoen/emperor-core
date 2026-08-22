"""
Social / public-platform post collector (distillation corpus scaffold).

Collects publicly available posts into the distillation corpus so the system can
study how humans (and AIs) discuss a topic. The primary source is the
**Hacker News Algolia Search API** -- keyless, CORS-friendly and stable.

Importing this module never touches the network. :func:`parse_hn_payload` is a
pure function so it can be unit-tested without I/O, while :meth:`SocialCollector.fetch`
is the async network path (guarded by an optional ``httpx`` dependency).

Adding more sources
-------------------
Implement a ``parse_<source>_payload(json) -> list[dict]`` pure normalizer that
returns entries shaped like ``{source, id, title, url, text, ts}``, then add an
``async def fetch_<source>(self, query, limit)`` method that performs the
request (guarded, async httpx) and calls ``self._ingest``. See ``fetch`` below
for the template. Candidates: Reddit (``/search.json`` keyless-ish), X/Twitter
(official API or nitter mirrors), Zhihu (unofficial endpoints), arXiv, Dev.to.

Flow into the distillation corpus
---------------------------------
``SocialCollector(store=DistillationStore(...))`` will, on every ``fetch``,
normalize entries and ingest them as :class:`DistillationTrace` records (with
``model_id="social/<source>"``, ``tier="social"``). They then live alongside
real model traces for downstream distillation.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

try:  # pragma: no cover - environment dependent
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

logger = logging.getLogger("jarvis.learning.social_collector")

HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"


def parse_hn_payload(payload: dict) -> list[dict]:
    """Normalize a Hacker News Algolia search response into corpus entries.

    Pure / deterministic -- no network. Each returned entry has the shape
    ``{source, id, title, url, text, ts}`` (``ts`` is an epoch-seconds int).
    """
    hits = payload.get("hits", []) if isinstance(payload, dict) else []
    entries: list[dict] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        entries.append(
            {
                "source": "hackernews",
                "id": hit.get("objectID") or hit.get("id") or "",
                "title": hit.get("title") or hit.get("story_title") or "",
                "url": hit.get("url") or hit.get("story_url") or "",
                "text": hit.get("story_text") or hit.get("comment_text") or "",
                "ts": hit.get("created_at_i") or 0,
            }
        )
    return entries


class SocialCollector:
    """Collects public posts from one or more sources into the corpus."""

    def __init__(self, store: Any = None) -> None:
        self._store = store

    async def fetch(self, query: str, limit: int = 20, tags: str = "story") -> list[dict]:
        """Fetch Hacker News stories matching ``query`` (async, network).

        Returns normalized entries ``{source, id, title, url, text, ts}``.
        Requires network access and the optional ``httpx`` dependency.
        """
        if httpx is None:
            raise RuntimeError("httpx is not installed; cannot fetch social posts")
        params = {"query": query, "tags": tags, "hitsPerPage": limit}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(HN_SEARCH_URL, params=params)
            resp.raise_for_status()
            entries = parse_hn_payload(resp.json())
        if self._store is not None:
            self._ingest(entries)
        return entries

    def _ingest(self, entries: list[dict]) -> None:
        """Flow collected items into the distillation store (if wired)."""
        if self._store is None:
            return
        try:
            from jarvis.learning.distillation_store import DistillationTrace

            for e in entries:
                self._store.record(
                    DistillationTrace(
                        ts=float(e.get("ts") or 0),
                        prompt=e.get("title", ""),
                        model_id="social/hackernews",
                        tier="social",
                        output=e.get("text", "") or e.get("title", ""),
                        latency_ms=0.0,
                        cost_estimate=0.0,
                        success=True,
                    )
                )
        except Exception:  # pragma: no cover - ingestion is best-effort
            logger.debug("social ingestion failed", exc_info=True)

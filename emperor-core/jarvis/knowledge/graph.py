"""
KnowledgeGraph — lightweight semantic graph for cross-domain knowledge.

Features:
- Entity extraction from natural language text
- Edge/relationship inference based on task co-occurrence
- Graph traversal (neighbors, paths, reachability)
- Topological queries (centrality, connected components)
- JSON-based snapshot persistence
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("jarvis.knowledge")

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    """A node in the knowledge graph."""

    id: str
    name: str
    type: str  # e.g. "domain", "action", "concept", "tool", "file"
    properties: dict[str, Any] = field(default_factory=dict)
    first_seen: str = ""
    last_seen: str = ""

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return False
        return self.id == other.id


@dataclass
class Edge:
    """A directed, weighted edge between two entities."""

    source: str  # entity id
    target: str  # entity id
    relation: str  # e.g. "uses", "depends_on", "produces", "related_to"
    weight: float = 1.0
    occurrences: int = 1
    first_seen: str = ""
    last_seen: str = ""


# ---------------------------------------------------------------------------
# Entity name normalizer
# ---------------------------------------------------------------------------

_ENTITY_PATTERN = re.compile(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)*|[a-z_]+(?:\.[a-z_]+)+|[A-Z][A-Z0-9_]+)\b")


def _extract_entities(text: str) -> list[tuple[str, str]]:
    """Extract (name, guessed_type) pairs from text.

    Uses pattern matching for:
    - Capitalized multi-word phrases → "concept"
    - dot.separated.lower → "module" / "domain"
    - UPPERCASE_UNDERSCORE → "constant"
    - Common nouns → filtered
    """
    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    # Dot-separated: jarvis.codex.engine → "domain" / "module"
    for match in re.finditer(r"\b([a-z_]+(?:\.[a-z_]+)+)\b", text):
        name = match.group(1)
        if name not in seen:
            entity_type = "domain" if name.startswith("jarvis.") else "module"
            results.append((name, entity_type))
            seen.add(name)

    # Capitalized phrases: "Code Review", "Machine Learning" → "concept"
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)\b", text):
        name = match.group(1)
        if name not in seen:
            results.append((name, "concept"))
            seen.add(name)

    # Single capitalized words → "concept" (filter very common ones)
    _stop = {"I", "A", "The", "This", "That", "It", "We", "You", "He", "She", "They",
             "Is", "Are", "Was", "Were", "Am", "Be", "Been", "Has", "Have", "Had",
             "Will", "Would", "Can", "Could", "Should", "May", "Might", "Shall",
             "Do", "Does", "Did", "Not", "No", "Yes", "Ok", "But", "And", "Or", "For"}
    for match in re.finditer(r"\b([A-Z][a-z]+)\b", text):
        name = match.group(1)
        if name not in seen and name not in _stop:
            results.append((name, "concept"))
            seen.add(name)

    return results


def _entity_id(name: str) -> str:
    """Deterministic entity ID from name."""
    return hashlib.sha256(name.lower().encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# KnowledgeGraph
# ---------------------------------------------------------------------------


class KnowledgeGraph:
    """Lightweight in-memory semantic graph.

    Usage:
        kg = KnowledgeGraph()
        await kg.ingest("JARVIS helps with Code Review using Vector Engine")
        await kg.ingest("Code Review involves analyzing Python files")
        neighbors = await kg.get_neighbors("code review")
        summary = kg.summary()
    """

    # Keywords that imply relationships when entities co-occur
    _RELATION_PATTERNS: list[tuple[str, str]] = [
        (r"\b(?:using|uses|via|through|with|powered by)\b", "uses"),
        (r"\b(?:depends on|requires|needs|relies on)\b", "depends_on"),
        (r"\b(?:produces|generates|creates|outputs|returns)\b", "produces"),
        (r"\b(?:involves|includes|contains|consists of)\b", "involves"),
        (r"\b(?:runs on|executes on|deploys to|hosted on)\b", "runs_on"),
    ]

    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.edges: dict[str, dict[str, Edge]] = {}  # source → {target: edge}
        self._reverse: dict[str, set[str]] = defaultdict(set)  # target → {source}

    # ------------------------------------------------------------------
    # Entity management
    # ------------------------------------------------------------------

    async def add_entity(
        self,
        name: str,
        entity_type: str = "concept",
        properties: Optional[dict[str, Any]] = None,
    ) -> Entity:
        """Add or update an entity. Returns the entity."""
        entity_id = _entity_id(name)
        now = datetime.now(timezone.utc).isoformat()

        if entity_id in self.entities:
            entity = self.entities[entity_id]
            entity.last_seen = now
            if properties:
                entity.properties.update(properties)
            return entity

        entity = Entity(
            id=entity_id,
            name=name,
            type=entity_type,
            properties=properties or {},
            first_seen=now,
            last_seen=now,
        )
        self.entities[entity_id] = entity
        logger.debug("Entity added: %s (%s)", name, entity_type)
        return entity

    async def get_entity(self, name: str) -> Optional[Entity]:
        """Look up entity by name (case-insensitive)."""
        return self.entities.get(_entity_id(name))

    async def get_or_create_entity(self, name: str, entity_type: str = "concept") -> Entity:
        """Get existing entity or create one."""
        entity = await self.get_entity(name)
        if entity:
            return entity
        return await self.add_entity(name, entity_type)

    # ------------------------------------------------------------------
    # Edge management
    # ------------------------------------------------------------------

    async def add_edge(
        self,
        source_name: str,
        target_name: str,
        relation: str = "related_to",
        weight: float = 1.0,
    ) -> Optional[Edge]:
        """Add or strengthen a directed edge between two entities.

        If the edge already exists, increment its occurrences and weight.
        """
        source_id = _entity_id(source_name)
        target_id = _entity_id(target_name)

        if source_id not in self.entities or target_id not in self.entities:
            logger.debug("Edge skipped: missing entity (%s → %s)", source_name, target_name)
            return None

        now = datetime.now(timezone.utc).isoformat()

        if source_id not in self.edges:
            self.edges[source_id] = {}

        existing = self.edges[source_id].get(target_id)
        if existing and existing.relation == relation:
            existing.occurrences += 1
            existing.weight += weight
            existing.last_seen = now
            return existing

        edge = Edge(
            source=source_id,
            target=target_id,
            relation=relation,
            weight=weight,
            occurrences=1,
            first_seen=now,
            last_seen=now,
        )
        self.edges[source_id][target_id] = edge
        self._reverse[target_id].add(source_id)
        logger.debug("Edge added: %s --[%s]→ %s", source_name, relation, target_name)
        return edge

    # ------------------------------------------------------------------
    # Ingestion (text → entities + edges)
    # ------------------------------------------------------------------

    async def ingest(self, text: str, domain: str = "general") -> list[Entity]:
        """Parse text, extract entities, infer relationships, and update the graph.

        Returns the list of entities extracted/updated.
        """
        extracted = _extract_entities(text)
        if not extracted:
            return []

        entities: list[Entity] = []
        for name, etype in extracted:
            entity = await self.get_or_create_entity(name, etype)
            entities.append(entity)

        # Detect relationships from co-occurring entities
        relation = self._detect_relation(text)
        for i, e1 in enumerate(entities):
            for e2 in entities[i + 1:]:
                # Create bidirectional edges
                await self.add_edge(e1.name, e2.name, relation)
                await self.add_edge(e2.name, e1.name, relation)

        return entities

    def _detect_relation(self, text: str) -> str:
        """Detect the primary relation from text using keyword patterns."""
        text_lower = text.lower()
        for pattern, relation in self._RELATION_PATTERNS:
            if re.search(pattern, text_lower):
                return relation
        return "related_to"

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_neighbors(
        self,
        entity_name: str,
        max_depth: int = 1,
        include_weights: bool = False,
    ) -> list[dict[str, Any]]:
        """Get neighboring entities up to max_depth hops.

        Returns list of {entity, relation, depth, [weight]} dicts.
        """
        entity_id = _entity_id(entity_name)
        if entity_id not in self.entities:
            return []

        visited: set[str] = {entity_id}
        queue: deque[tuple[str, int]] = deque([(entity_id, 0)])
        results: list[dict[str, Any]] = []

        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue

            neighbors = self.edges.get(current, {})
            for target_id, edge in neighbors.items():
                if target_id in visited:
                    continue
                visited.add(target_id)
                target = self.entities.get(target_id)
                if target is None:
                    continue

                entry: dict[str, Any] = {
                    "entity": target.name,
                    "type": target.type,
                    "relation": edge.relation,
                    "depth": depth + 1,
                }
                if include_weights:
                    entry["weight"] = round(edge.weight, 3)
                    entry["occurrences"] = edge.occurrences
                results.append(entry)
                queue.append((target_id, depth + 1))

        return results

    async def find_paths(
        self,
        source_name: str,
        target_name: str,
        max_depth: int = 4,
    ) -> list[list[dict[str, str]]]:
        """Find all paths between two entities up to max_depth (BFS).

        Each path is a list of {entity, relation} steps.
        """
        src_id = _entity_id(source_name)
        tgt_id = _entity_id(target_name)

        if src_id not in self.entities or tgt_id not in self.entities:
            return []

        if src_id == tgt_id:
            return [[{"entity": source_name, "relation": "self"}]]

        # BFS to find all shortest paths
        queue: deque[tuple[str, list[dict[str, str]]]] = deque(
            [(_entity_id(source_name), [{"entity": source_name, "relation": "start"}])]
        )
        paths: list[list[dict[str, str]]] = []
        visited_depth: dict[str, int] = {src_id: 0}

        while queue:
            current_id, path = queue.popleft()
            current_depth = len(path) - 1

            if current_depth > max_depth:
                continue

            if current_id == tgt_id:
                paths.append(path)
                continue

            neighbors = self.edges.get(current_id, {})
            for neighbor_id, edge in neighbors.items():
                new_depth = current_depth + 1
                if neighbor_id in visited_depth and visited_depth[neighbor_id] < new_depth:
                    continue
                visited_depth[neighbor_id] = new_depth

                entity = self.entities.get(neighbor_id)
                if entity is None:
                    continue
                new_step = {"entity": entity.name, "relation": edge.relation}
                queue.append((neighbor_id, path + [new_step]))

        return paths

    async def most_central(self, top_n: int = 10) -> list[dict[str, Any]]:
        """Return entities ranked by degree centrality (in + out edges)."""
        scores: dict[str, float] = {}

        for entity_id, entity in self.entities.items():
            out_degree = len(self.edges.get(entity_id, {}))
            in_degree = len(self._reverse.get(entity_id, set()))
            scores[entity.name] = out_degree + in_degree

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [{"entity": name, "centrality": score} for name, score in ranked]

    # ------------------------------------------------------------------
    # Graph summary & persistence
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Return a graph-level summary."""
        return {
            "entity_count": len(self.entities),
            "edge_count": sum(len(targets) for targets in self.edges.values()),
            "entity_types": self._type_distribution(),
            "top_entities": [
                {"name": e.name, "type": e.type}
                for e in sorted(self.entities.values(), key=lambda e: e.last_seen, reverse=True)[:10]
            ],
        }

    def _type_distribution(self) -> dict[str, int]:
        dist: dict[str, int] = defaultdict(int)
        for entity in self.entities.values():
            dist[entity.type] += 1
        return dict(dist)

    async def save_snapshot(self, file_path: str) -> None:
        """Save a JSON snapshot of the graph to disk."""
        data = {
            "entities": [
                {
                    "id": e.id,
                    "name": e.name,
                    "type": e.type,
                    "properties": e.properties,
                    "first_seen": e.first_seen,
                    "last_seen": e.last_seen,
                }
                for e in self.entities.values()
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "relation": edge.relation,
                    "weight": edge.weight,
                    "occurrences": edge.occurrences,
                    "first_seen": edge.first_seen,
                    "last_seen": edge.last_seen,
                }
                for targets in self.edges.values()
                for edge in targets.values()
            ],
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Knowledge graph snapshot saved → %s (%d entities, %d edges)",
                     file_path, len(data["entities"]), len(data["edges"]))

    async def load_snapshot(self, file_path: str) -> None:
        """Load a graph from a JSON snapshot."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Snapshot not found: {file_path}")

        data = json.loads(path.read_text(encoding="utf-8"))
        self.entities.clear()
        self.edges.clear()
        self._reverse.clear()

        for e_data in data.get("entities", []):
            entity = Entity(
                id=e_data["id"],
                name=e_data["name"],
                type=e_data["type"],
                properties=e_data.get("properties", {}),
                first_seen=e_data.get("first_seen", ""),
                last_seen=e_data.get("last_seen", ""),
            )
            self.entities[entity.id] = entity

        for e_data in data.get("edges", []):
            edge = Edge(
                source=e_data["source"],
                target=e_data["target"],
                relation=e_data["relation"],
                weight=e_data.get("weight", 1.0),
                occurrences=e_data.get("occurrences", 1),
                first_seen=e_data.get("first_seen", ""),
                last_seen=e_data.get("last_seen", ""),
            )
            if edge.source not in self.edges:
                self.edges[edge.source] = {}
            self.edges[edge.source][edge.target] = edge
            self._reverse[edge.target].add(edge.source)

        logger.info("Knowledge graph loaded from snapshot: %d entities, %d edges",
                     len(self.entities), sum(len(t) for t in self.edges.values()))

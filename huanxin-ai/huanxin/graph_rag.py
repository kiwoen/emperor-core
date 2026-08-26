"""
GraphRAG Memory Engine — Knowledge Graph retrieval for 幻炘AI.

Turns Agent memory from simple Chunk retrieval into knowledge-graph-based
retrieval, capable of handling cross-document complex entity relationships.

Core components:
- Entity extraction via regex rules (no LLM dependency)
- Relation building via co-occurrence windows
- Hybrid search: keyword match + graph traversal (1-2 hop neighbors)
- Subgraph query, entity summaries, and graph statistics

Data Model:
    Entity: name, type, properties, source_documents
    Relation: source_entity, target_entity, relation_type, description, confidence
    KnowledgeGraph: in-memory storage backed by dict/set
"""

from __future__ import annotations

import heapq
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("huanxin.graph_rag")


# ═══════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════

# Entity type classification patterns
_PATTERNS = {
    "person": re.compile(
        r"\b(?:Dr\.|Mr\.|Mrs\.|Ms\.|Prof\.|President|CEO|CTO|CFO|Scientist)"
        r"\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b"
    ),
    "organization": re.compile(
        r"\b(?:Inc\.|LLC|Ltd\.|Corp\.|Corporation|Company|Foundation|Institute"
        r"|University|Lab|Laboratory|Agency|Department|Organization|Group|Team)\b"
    ),
    "code_identifier": re.compile(
        r"\b(?:[A-Z][a-zA-Z0-9]*_)+\b"           # snake_case identifiers starting with uppercase
        r"|\b[a-z]+(?:_[a-z]+)+\b"               # pure snake_case (lowercase)
        r"|\b(?:class|def|function)\s+([A-Za-z_]\w*)\b"  # function/class names
        r"|\b(?:[a-z]+(?:[A-Z][a-z0-9]*)+)\b"    # camelCase
        r"|\b(?:[A-Z][a-z0-9]*(?:[A-Z][a-z0-9]*)+)\b"  # PascalCase
    ),
    "date": re.compile(
        r"\b\d{4}-\d{2}-\d{2}\b"                   # 2024-01-15
        r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4}\b"
    ),
    "version": re.compile(r"\bv?\d+\.\d+(?:\.\d+)?(?:-[a-zA-Z0-9]+)?\b"),
    "concept": re.compile(
        r"\b(?:[A-Z][a-z]+_)+\b"                    # snake_case concepts
        r"|\b[A-Z][a-zA-Z0-9]*(?:-[A-Z0-9][a-zA-Z0-9]*)+\b"  # hyphenated (GPT-4, BERT-large)
        r"|\b[A-Z]{2,}(?:_[A-Z]{2,})*\b"           # CONSTANTS / ACRONYMS
        r"|\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b" # Proper Noun Phrases (e.g. "GraphRAG")
    ),
}

# Known organization keywords (case-insensitive match for entity detection)
_ORG_KEYWORDS = {
    "inc", "llc", "ltd", "corp", "corporation", "company", "foundation",
    "institute", "university", "lab", "laboratory", "agency", "department",
    "organization", "group", "team", "google", "microsoft", "openai",
    "anthropic", "meta", "deepseek", "langchain", "crewai", "metagpt",
    "salesforce", "amazon", "apple", "netflix", "nvidia", "intel", "ibm",
    "huggingface", "github", "gitlab",
}

# Person title prefixes
_PERSON_TITLES = {
    "dr.", "mr.", "mrs.", "ms.", "prof.", "professor", "president",
    "ceo", "cto", "cfo", "scientist", "engineer", "researcher",
}

# Known proper nouns (single capitalized words that are entities)
_KNOWN_ENTITIES = {
    # People
    "Turing", "Curie", "Hinton", "Hippocrates", "Confucius", "Tesla",
    "Franklin", "Lovelace", "Einstein", "Newton", "Darwin", "Hawking",
    "Sutton", "LeCun", "Bengio", "Goodfellow", "Vaswani", "Hochreiter",
    # Organizations / Products
    "ChatGPT", "GPT", "Claude", "Gemini", "DeepSeek", "Llama", "Mistral",
    "Falcon", "BERT", "RoBERTa", "T5", "PaLM", "LLaMA",
    # Concepts
    "GraphRAG", "RAG", "RLHF", "DPO", "LoRA", "PPO", "DQN", "CNN", "RNN",
    "LSTM", "Transformer", "GNN", "SGD", "Adam", "ReLU", "GELU", "SiLU",
    "Softmax", "LayerNorm", "BatchNorm", "Dropout",
    # Protocols & Standards
    "MCP", "HTTP", "REST", "GraphQL", "gRPC", "WebSocket", "OAuth",
    "JWT", "SQL", "JSON", "YAML", "TOML", "CSV", "XML",
    "Python", "JavaScript", "TypeScript", "Rust", "Go", "Java", "C++",
    "Docker", "Kubernetes", "Terraform", "Helm",
}


def _classify_entity(name: str) -> str:
    """Classify an entity string into its most likely type."""
    lower = name.lower()

    # Check organizational keywords
    if any(kw in lower.split() for kw in _ORG_KEYWORDS):
        return "organization"
    if lower in _ORG_KEYWORDS:
        return "organization"

    # Check person titles
    if any(lower.startswith(t) for t in _PERSON_TITLES):
        return "person"

    # Date patterns
    if re.match(r"^\d{4}-\d{2}-\d{2}$", name):
        return "date"

    # Version patterns
    if re.match(r"^v?\d+\.\d+(?:\.\d+)?", name):
        return "version"

    # Code identifiers
    if re.match(r"^[a-z]+(?:[A-Z][a-z0-9]*)+$", name):  # camelCase
        return "code_identifier"
    if re.match(r"^[A-Z][a-z0-9]*(?:[A-Z][a-z0-9]*)+$", name):  # PascalCase
        return "code_identifier"
    if re.match(r"^[A-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+$", name):  # snake_case with caps
        return "code_identifier"
    if re.match(r"^[a-z]+(_[a-z]+)+$", name):  # pure snake_case
        return "code_identifier"

    # Acronym detection (all caps, 2-7 chars)
    if re.match(r"^[A-Z]{2,7}$", name):
        return "concept"

    # Known proper nouns
    if name in _KNOWN_ENTITIES:
        return "concept"

    # Multi-word capitalized phrases — likely concept or organization
    if " " in name and all(w[0].isupper() and w[1:].islower() for w in name.split() if w):
        if len(name.split()) <= 2:
            return "person"
        return "concept"

    # Single capitalized word — default to concept
    if name[0].isupper() and len(name) > 1:
        return "concept"

    return "concept"


# ═══════════════════════════════════════════════════════════════════════
# Data Structures
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class Entity:
    """A node in the knowledge graph."""

    name: str
    type: str = "concept"  # person / organization / concept / event / tool / location
    properties: dict[str, Any] = field(default_factory=dict)
    source_documents: list[str] = field(default_factory=list)

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Entity):
            return False
        return self.name == other.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "properties": self.properties,
            "source_documents": self.source_documents,
        }


@dataclass
class Relation:
    """An edge between two entities in the knowledge graph."""

    source_entity: str
    target_entity: str
    relation_type: str = "co_occurrence"
    description: str = ""
    confidence: float = 0.5

    def __hash__(self) -> int:
        return hash((self.source_entity, self.target_entity, self.relation_type))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Relation):
            return False
        return (
            self.source_entity == other.source_entity
            and self.target_entity == other.target_entity
            and self.relation_type == other.relation_type
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_entity,
            "target": self.target_entity,
            "relation_type": self.relation_type,
            "description": self.description,
            "confidence": self.confidence,
        }


@dataclass
class GraphFragment:
    """A subgraph centered on a specific entity."""

    center: Entity
    entities: list[Entity]
    relations: list[Relation]
    depth: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "center": self.center.to_dict(),
            "entities": [e.to_dict() for e in self.entities],
            "relations": [r.to_dict() for r in self.relations],
            "depth": self.depth,
        }


@dataclass
class SearchResult:
    """Result from a GraphRAG search."""

    entity: Entity
    score: float
    source: str = "keyword"  # keyword / neighbor / both
    hops: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity.to_dict(),
            "score": self.score,
            "source": self.source,
            "hops": self.hops,
        }


# ═══════════════════════════════════════════════════════════════════════
# Knowledge Graph
# ═══════════════════════════════════════════════════════════════════════


class KnowledgeGraph:
    """In-memory knowledge graph with entity and relation storage."""

    def __init__(self) -> None:
        self.entities: dict[str, Entity] = {}
        self.relations: list[Relation] = []
        # Adjacency for fast graph traversal
        self._adj: dict[str, set[str]] = defaultdict(set)

    def add_entity(self, entity: Entity) -> None:
        """Add or merge an entity."""
        if entity.name in self.entities:
            existing = self.entities[entity.name]
            existing.type = entity.type
            existing.properties.update(entity.properties)
            for doc in entity.source_documents:
                if doc not in existing.source_documents:
                    existing.source_documents.append(doc)
        else:
            self.entities[entity.name] = entity

    def add_relation(self, relation: Relation) -> None:
        """Add a relation if not already present."""
        if relation in self.relations:
            return
        self.relations.append(relation)
        self._adj[relation.source_entity].add(relation.target_entity)
        self._adj[relation.target_entity].add(relation.source_entity)

    def get_neighbors(self, entity_name: str, hops: int = 1) -> list[str]:
        """Get neighboring entity names up to N hops."""
        if entity_name not in self._adj:
            return []

        visited: set[str] = {entity_name}
        frontier: set[str] = {entity_name}
        for _ in range(hops):
            next_frontier: set[str] = set()
            for node in frontier:
                for neighbor in self._adj.get(node, set()):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier
            if not frontier:
                break

        visited.discard(entity_name)
        return list(visited)

    def get_entity_degree(self, entity_name: str) -> int:
        """Return the degree (number of relations) for an entity."""
        return len(self._adj.get(entity_name, set()))

    def get_relations_for(self, entity_name: str) -> list[Relation]:
        """Get all relations involving an entity."""
        return [
            r for r in self.relations
            if r.source_entity == entity_name or r.target_entity == entity_name
        ]

    def get_relations_between(
        self, entity_a: str, entity_b: str
    ) -> list[Relation]:
        """Get relations between two specific entities."""
        return [
            r for r in self.relations
            if (r.source_entity == entity_a and r.target_entity == entity_b)
            or (r.source_entity == entity_b and r.target_entity == entity_a)
        ]

    def subgraph(self, center: str, hops: int = 1) -> GraphFragment:
        """Extract a subgraph centered on an entity."""
        center_entity = self.entities.get(center)
        if center_entity is None:
            return GraphFragment(
                center=Entity(name=center),
                entities=[],
                relations=[],
                depth=hops,
            )

        neighbors = self.get_neighbors(center, hops)
        sub_entities: list[Entity] = []
        sub_relations: list[Relation] = []

        entity_set: set[str] = {center} | set(neighbors)
        for name in entity_set:
            ent = self.entities.get(name)
            if ent:
                sub_entities.append(ent)

        for rel in self.relations:
            if rel.source_entity in entity_set and rel.target_entity in entity_set:
                sub_relations.append(rel)

        return GraphFragment(
            center=center_entity,
            entities=sub_entities,
            relations=sub_relations,
            depth=hops,
        )

    def stats(self) -> dict[str, Any]:
        """Return KG statistics."""
        degree_counts = [
            self.get_entity_degree(name) for name in self.entities
        ]
        return {
            "entity_count": len(self.entities),
            "relation_count": len(self.relations),
            "avg_degree": sum(degree_counts) / max(len(degree_counts), 1),
            "max_degree": max(degree_counts) if degree_counts else 0,
        }


# ═══════════════════════════════════════════════════════════════════════
# Entity Extraction Helpers
# ═══════════════════════════════════════════════════════════════════════


def _extract_entities(text: str) -> list[Entity]:
    """Extract entities from text using regex rules.

    Strategies:
    1. Known proper nouns from _KNOWN_ENTITIES
    2. Regex patterns for person names, organizations, code identifiers, etc.
    3. Capitalized multi-word phrases
    """
    found: dict[str, Entity] = {}

    # 1. Match known entities first (case-sensitive)
    for known in _KNOWN_ENTITIES:
        # Match as whole word or with surrounding punctuation
        pattern = re.compile(r"(?<![a-zA-Z0-9])" + re.escape(known) + r"(?![a-zA-Z0-9])")
        if pattern.search(text):
            found[known] = Entity(
                name=known,
                type=_classify_entity(known),
            )

    # 2. Regex-based extraction
    for entity_type, pat in _PATTERNS.items():
        for match in pat.finditer(text):
            name = match.group(0).strip()
            if len(name) < 2:
                continue
            # Skip if too generic
            if name.lower() in {"the", "a", "an", "is", "it", "in", "on", "at", "to", "of", "for", "and", "or"}:
                continue
            # Skip version-only matches that are too short (like "v1")
            if entity_type == "version" and len(name) <= 3:
                continue

            real_type = entity_type
            if entity_type == "concept" and _classify_entity(name) != "concept":
                real_type = _classify_entity(name)

            if name not in found:
                found[name] = Entity(name=name, type=real_type)

    # 3. Capitalized proper noun phrases (not captured by regex)
    # Match 2-4 word capitalized sequences
    proper_phrase = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\b")
    for match in proper_phrase.finditer(text):
        name = match.group(1).strip()
        if name in found or len(name) < 5:
            continue
        found[name] = Entity(name=name, type="concept")

    return list(found.values())


def _extract_relations(
    entities: list[Entity],
    sentences: list[str],
    window_size: int = 5,
) -> list[Relation]:
    """Build relations based on co-occurrence within a sliding window of sentences.

    Two entities appearing within the same window are connected.
    Confidence scales with proximity.
    """
    entity_names = {e.name for e in entities}
    relations: dict[tuple[str, str], Relation] = {}

    # Collect sentences containing at least one entity
    for window_start in range(0, len(sentences), max(1, window_size // 2)):
        window_end = min(window_start + window_size, len(sentences))
        window_text = " ".join(sentences[window_start:window_end])
        window_entities: list[str] = []

        for name in entity_names:
            if name in window_text:
                window_entities.append(name)

        # Build pairwise relations
        for i in range(len(window_entities)):
            for j in range(i + 1, len(window_entities)):
                a, b = sorted([window_entities[i], window_entities[j]])
                key = (a, b)
                # Confidence: higher for entities that appear in more windows together
                if key not in relations:
                    # Look for proximity within the window
                    pos_a = window_text.find(window_entities[i])
                    pos_b = window_text.find(window_entities[j])
                    proximity = 1.0 / (1.0 + abs(pos_a - pos_b) / 100.0)
                    confidence = min(proximity * 0.9, 0.95)
                    rel = Relation(
                        source_entity=a,
                        target_entity=b,
                        relation_type="co_occurrence",
                        description=f"Co-occurs with {b if a == window_entities[i] else a}",
                        confidence=confidence,
                    )
                    relations[key] = rel
                else:
                    # Boost confidence for repeated co-occurrence
                    relations[key].confidence = min(relations[key].confidence + 0.1, 1.0)

    return list(relations.values())


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences using simple heuristics."""
    # Split on common sentence delimiters
    raw = re.split(r"(?<=[.!?;])\s+", text)
    # Further split long sentences on line breaks or certain sub-delimiters
    result: list[str] = []
    for s in raw:
        s = s.strip()
        if not s:
            continue
        if len(s) > 500:
            # Split long sentences on commas or colons
            sub = re.split(r"(?<=[,:])\s+", s)
            result.extend(p.strip() for p in sub if p.strip())
        else:
            result.append(s)
    return result


# ═══════════════════════════════════════════════════════════════════════
# GraphRAG Engine
# ═══════════════════════════════════════════════════════════════════════


class GraphRAG:
    """GraphRAG memory engine.

    Extracts entities and relations from documents, builds an in-memory
    knowledge graph, and provides hybrid search (keyword + graph traversal).

    Usage:
        >>> graf = GraphRAG()
        >>> graf.add_document("doc1", "GPT-4 is a large language model by OpenAI.")
        >>> results = graf.search("GPT-4")
        >>> fragment = graf.query_graph("OpenAI", hops=1)
        >>> graf.stats()
    """

    def __init__(self) -> None:
        self._kg = KnowledgeGraph()
        self._documents: dict[str, str] = {}
        # Inverted index: entity name → set of document IDs
        self._entity_docs: dict[str, set[str]] = defaultdict(set)

    # ── Document Ingestion ────────────────────────────────────────────

    def add_document(self, doc_id: str, text: str) -> list[Entity]:
        """Extract entities and relations from a document and ingest into the graph.

        Returns the list of extracted entities.
        """
        self._documents[doc_id] = text
        sentences = _split_sentences(text)
        entities = _extract_entities(text)

        # Tag entities with this document
        for ent in entities:
            ent.source_documents.append(doc_id)
            self._entity_docs[ent.name].add(doc_id)

        # Build relations
        relations = _extract_relations(entities, sentences)

        # Ingest into knowledge graph
        for ent in entities:
            self._kg.add_entity(ent)
        for rel in relations:
            self._kg.add_relation(rel)

        logger.debug(
            "GraphRAG ingested doc '%s': %d entities, %d relations",
            doc_id, len(entities), len(relations),
        )
        return entities

    # ── Search ─────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 10,
        expand_hops: int = 2,
    ) -> list[SearchResult]:
        """Hybrid search combining keyword match with graph traversal.

        1. Keyword match: find entities whose name/description matches the query.
        2. Graph expansion: from matched entities, traverse 1-2 hop neighbors.
        3. Merge, deduplicate, and score.

        Args:
            query: Search query string.
            top_k: Max results to return.
            expand_hops: Graph traversal depth (1-2).

        Returns:
            Sorted list of SearchResult.
        """
        query_lower = query.lower()
        query_terms = set(query_lower.split())

        # Phase 1: Keyword match against entity names and properties
        scored: dict[str, tuple[float, str, int]] = {}  # name → (score, source, hops)

        for name, entity in self._kg.entities.items():
            name_lower = name.lower()
            score = 0.0

            # Exact name match
            if query_lower == name_lower:
                score = 1.0
            # Name contains query
            elif query_lower in name_lower:
                score = 0.85
            # Query contains entity name
            elif name_lower in query_lower:
                score = 0.80
            # Partial token overlap
            else:
                name_terms = set(name_lower.split())
                overlap = query_terms & name_terms
                if overlap:
                    score = len(overlap) / max(len(query_terms), 1) * 0.70

            # Also check properties for keyword match
            if score < 0.5:
                prop_text = " ".join(str(v) for v in entity.properties.values()).lower()
                if query_lower in prop_text:
                    score = 0.60

            if score > 0:
                scored[name] = (score, "keyword", 0)

        # Phase 2: Graph traversal — expand from matched entities
        if expand_hops > 0:
            seed_entities = list(scored.keys())
            for seed in seed_entities:
                for hop in range(1, expand_hops + 1):
                    neighbors = self._kg.get_neighbors(seed, hops=1)
                    for neighbor in neighbors:
                        if neighbor not in scored:
                            # Neighbor gets a discounted score based on seed
                            seed_score = scored[seed][0]
                            neighbor_score = seed_score * (0.6 ** hop)
                            scored[neighbor] = (neighbor_score, "neighbor", hop)
                    # Expand frontier
                    seed_entities = neighbors

        # Phase 3: Build results, sort, and return top_k
        heap: list[tuple[float, int, str, str, int]] = []
        for name, (score, source, hops) in scored.items():
            entity = self._kg.entities.get(name)
            if entity is None:
                continue
            # Boost by entity degree (well-connected entities are more relevant)
            degree_boost = min(self._kg.get_entity_degree(name) * 0.02, 0.15)
            final_score = min(score + degree_boost, 1.0)
            # Use negative for max-heap
            heapq.heappush(heap, (-final_score, hops, name, source, hops))

        results: list[SearchResult] = []
        while heap and len(results) < top_k:
            neg_score, _, name, source, hops = heapq.heappop(heap)
            entity = self._kg.entities[name]
            results.append(SearchResult(
                entity=entity,
                score=-neg_score,
                source=source,
                hops=hops,
            ))

        return results

    # ── Graph Queries ──────────────────────────────────────────────────

    def query_graph(self, entity_name: str, hops: int = 1) -> GraphFragment:
        """Get a subgraph fragment centered on the given entity."""
        return self._kg.subgraph(entity_name, hops)

    def get_related_entities(
        self, entity_name: str, relation_type: str | None = None,
    ) -> list[Entity]:
        """Get entities related to the given entity, optionally filtered by relation type."""
        relations = self._kg.get_relations_for(entity_name)
        if relation_type:
            relations = [r for r in relations if r.relation_type == relation_type]

        related: dict[str, Entity] = {}
        for rel in relations:
            other = rel.target_entity if rel.source_entity == entity_name else rel.source_entity
            if other in self._kg.entities and other not in related:
                related[other] = self._kg.entities[other]

        return list(related.values())

    def summarize_entity(self, entity_name: str) -> str:
        """Generate a human-readable summary of an entity including its properties and relations."""
        entity = self._kg.entities.get(entity_name)
        if entity is None:
            return f"Entity '{entity_name}' not found in graph."

        parts: list[str] = [
            f"Entity: {entity.name}",
            f"Type: {entity.type}",
            f"Documents: {len(entity.source_documents)}",
        ]
        if entity.properties:
            parts.append(f"Properties: {entity.properties}")

        relations = self._kg.get_relations_for(entity_name)
        if relations:
            parts.append(f"Relations ({len(relations)}):")
            for rel in relations:
                other = rel.target_entity if rel.source_entity == entity_name else rel.source_entity
                direction = "→" if rel.source_entity == entity_name else "←"
                parts.append(f"  {direction} {other} [{rel.relation_type}] (conf={rel.confidence:.2f})")
        else:
            parts.append("Relations: none")

        return "\n".join(parts)

    # ── Stats ──────────────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        """Return comprehensive graph statistics."""
        base = self._kg.stats()
        base["document_count"] = len(self._documents)

        # Top entities by degree
        degrees = [
            (name, self._kg.get_entity_degree(name))
            for name in self._kg.entities
        ]
        degrees.sort(key=lambda x: x[1], reverse=True)
        base["top_entities"] = [
            {"name": name, "degree": deg, "type": self._kg.entities[name].type}
            for name, deg in degrees[:20]
        ]

        # Entity type distribution
        type_counts: dict[str, int] = defaultdict(int)
        for ent in self._kg.entities.values():
            type_counts[ent.type] += 1
        base["type_distribution"] = dict(type_counts)

        return base

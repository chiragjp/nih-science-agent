"""A typed knowledge graph over awards, publications, trials, and entities.

Thin wrapper around a NetworkX ``MultiDiGraph``. Nodes are ``"TYPE:id"`` strings
(e.g. ``"AWARD:R01ES032470"``) so identity is globally unambiguous; every edge
carries the linkage layer's provenance fields (source, method, authoritative,
confidence). The public surface is deliberately backend-agnostic — ``add_edge``,
``neighbors``, ``nodes_of_type``, ``stats`` — so the NetworkX store can later be
swapped for Kuzu or Neo4j without changing callers.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import networkx as nx

from nih_science_agent.linkage.edges import Edge


def split_node(node_id: str) -> tuple[str, str]:
    """Split a ``"TYPE:id"`` node id into ``(type, id)``."""
    node_type, _, ident = node_id.partition(":")
    return node_type, ident


class KnowledgeGraph:
    def __init__(self) -> None:
        self.g = nx.MultiDiGraph()

    # -- mutation --------------------------------------------------------- #

    def add_node(self, node_id: str, **attrs: Any) -> None:
        node_type, _ = split_node(node_id)
        if node_id in self.g:
            self.g.nodes[node_id].update({k: v for k, v in attrs.items() if v is not None})
        else:
            self.g.add_node(node_id, node_type=node_type, **attrs)

    def add_edge(self, edge: Edge) -> None:
        """Add an :class:`Edge`, creating its endpoint nodes if absent."""
        for nid in (edge.subject, edge.object):
            if nid not in self.g:
                self.add_node(nid)
        # key=predicate keeps parallel relations between the same pair distinct.
        self.g.add_edge(
            edge.subject,
            edge.object,
            key=edge.predicate,
            predicate=edge.predicate,
            source=edge.source,
            method=edge.method,
            authoritative=edge.authoritative,
            confidence=edge.confidence,
            evidence_pointer=edge.evidence_pointer,
            retrieved_at=edge.retrieved_at,
        )

    # -- queries ---------------------------------------------------------- #

    def node_type(self, node_id: str) -> str | None:
        return self.g.nodes[node_id].get("node_type") if node_id in self.g else None

    def nodes_of_type(self, node_type: str) -> list[str]:
        return [n for n, d in self.g.nodes(data=True) if d.get("node_type") == node_type]

    def neighbors(
        self, node_id: str, predicate: str | None = None, node_type: str | None = None
    ) -> list[str]:
        """Outgoing neighbors of ``node_id``, optionally filtered by predicate/type."""
        if node_id not in self.g:
            return []
        out: list[str] = []
        for _, dst, data in self.g.out_edges(node_id, data=True):
            if predicate and data.get("predicate") != predicate:
                continue
            if node_type and self.node_type(dst) != node_type:
                continue
            out.append(dst)
        return out

    @property
    def num_nodes(self) -> int:
        return self.g.number_of_nodes()

    @property
    def num_edges(self) -> int:
        return self.g.number_of_edges()

    def stats(self) -> dict[str, Any]:
        node_types = Counter(d.get("node_type") for _, d in self.g.nodes(data=True))
        predicates = Counter(d.get("predicate") for *_, d in self.g.edges(data=True))
        authoritative = sum(1 for *_, d in self.g.edges(data=True) if d.get("authoritative"))
        return {
            "nodes": self.num_nodes,
            "edges": self.num_edges,
            "node_types": dict(node_types),
            "predicates": dict(predicates),
            "authoritative_edges": authoritative,
            "inferred_edges": self.num_edges - authoritative,
        }

    def to_dict(self) -> dict[str, Any]:
        """Backend-independent serialization (nodes + edges with attributes)."""
        return {
            "nodes": [{"id": n, **d} for n, d in self.g.nodes(data=True)],
            "edges": [{"subject": u, "object": v, **d} for u, v, d in self.g.edges(data=True)],
        }

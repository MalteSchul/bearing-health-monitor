"""A small knowledge graph about the rig, the detector and bearing damage, for the copilot.

Held in memory with networkx: about 50 read-only facts need no database. The data model is a
graph database's (labelled nodes, typed arrows), so Neo4j could hold the same file unchanged.
"""

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import networkx as nx

# Two hops reach from a part to its damage types and their causes, or from a status to the rules
# and actions behind it. A third would pull in most of the graph.
HOPS = 2
# Sent with every question, whatever it is about.
MACHINE = "Machine"


@dataclass(frozen=True)
class Fact:
    id: str
    label: str
    text: str
    # The full citation, not the short key the file uses.
    source: str


class Knowledge:
    def __init__(self, graph: nx.DiGraph) -> None:
        self._graph = graph

    @classmethod
    def load(cls, path: Path | None = None) -> "Knowledge":
        """Read the knowledge file; a broken arrow or source stops the app from starting."""
        raw = (
            path.read_bytes() if path else files("monitor").joinpath("knowledge.toml").read_bytes()
        )
        data = tomllib.loads(raw.decode("utf-8"))
        sources: dict[str, str] = data["sources"]
        graph: nx.DiGraph[str] = nx.DiGraph()
        for node in data["node"]:
            if node["source"] not in sources:
                raise ValueError(f"{node['id']!r} cites unknown source {node['source']!r}")
            if node["id"] in graph:
                raise ValueError(f"duplicate node {node['id']!r}")
            graph.add_node(
                node["id"], label=node["label"], text=node["text"], source=sources[node["source"]]
            )
        for node in data["node"]:
            for relation, targets in node.get("out", {}).items():
                for target in targets:
                    if target not in graph:
                        raise ValueError(f"{node['id']!r} points to unknown node {target!r}")
                    graph.add_edge(node["id"], target, type=relation)
        return cls(graph)

    def __contains__(self, node: str) -> bool:
        return node in self._graph

    def fact(self, node: str) -> Fact:
        attributes = self._graph.nodes[node]
        return Fact(
            id=node, label=attributes["label"], text=attributes["text"], source=attributes["source"]
        )

    def machine(self) -> list[Fact]:
        return [self.fact(n) for n, label in self._graph.nodes(data="label") if label == MACHINE]

    def around(self, starts: Iterable[str]) -> list[tuple[Fact, list[str]]]:
        """Each start and what its arrows reach within HOPS, nearest first, without repeats, each
        with the starts that reached it: why it was looked up, which a bare fact cannot say.

        Arrows are only followed forwards, from what the detector reports towards what explains it.
        In Cypher: MATCH (s {id: $start})-[*0..2]->(n) RETURN n, collect(s.id).
        """
        reached_from: dict[str, list[str]] = {}
        for start in starts:
            # Breadth-first, so the dict comes back ordered by distance.
            for node in nx.single_source_shortest_path_length(self._graph, start, cutoff=HOPS):
                origins = reached_from.setdefault(node, [])
                if start not in origins:
                    origins.append(start)
        return [(self.fact(n), origins) for n, origins in reached_from.items()]

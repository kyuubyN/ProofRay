# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D45 bounded authorized semantic hypergraph and D40 transport."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable

from .authorized_semantic_ir import SemanticSource, SemanticTerm
from .sigma_pba import AuthorizedFact, SealedSource, SigmaPBAExecutor


RULE = "d45.authorized-semantic-hypergraph.v1"
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_POLARITIES = frozenset({"positive", "negative"})
_OPERATORS = {
    "entity_bundle": frozenset({"name", "type", "value", "quantifier", "modifier"}),
    "coord_and": frozenset({"member"}),
    "coord_or": frozenset({"member"}),
    "compound": frozenset({"head", "modifier"}),
    "quantified": frozenset({"entity", "quantifier"}),
}
_COMMUTATIVE = frozenset({"coord_and", "coord_or"})
_ROLE = re.compile(r"ARG[1-4]")
_MAX_SOURCE_BYTES = 1_000_000
_MAX_LEAVES = 10_000
_MAX_SYMBOLS = 10_000
_MAX_NODES = 10_000
_MAX_EVENTS = 2_000
_MAX_EDGES = 40_000
_MAX_DEPTH = 64


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + b"\x00" + _canonical(value).encode()).hexdigest()


def _reference(kind: str, payload: object) -> str:
    return f"d45:{kind}:{_digest(f'horizon-d45-{kind}-v1'.encode(), payload)}"


def _schema_reference(kind: str, value: str) -> str:
    return _reference(kind, {"value": value})


def structured_term_reference(operator: str, edges: Iterable[tuple[str, str]],
                              operator_ref: str | None = None) -> str:
    """Build an internal query/graph term identity under the frozen D45 law."""
    canonical_edges = tuple(sorted((str(role), str(child)) for role, child in edges))
    if operator not in _OPERATORS or not canonical_edges or any(
            role not in _OPERATORS[operator] for role, _child in canonical_edges):
        raise ValueError("invalid D45 structured term reference")
    if operator in _COMMUTATIVE and (len(canonical_edges) < 2 or
                                     {role for role, _ in canonical_edges} != {"member"}):
        raise ValueError("coordination reference requires two or more members")
    return _reference("term", {"operator": operator, "operator_ref": operator_ref,
                               "edges": canonical_edges})


def charge_reference(polarity: str, modalities: Iterable[str] = ()) -> str:
    if polarity not in _POLARITIES:
        raise ValueError("invalid D45 charge polarity")
    return _reference("charge", {"polarity": polarity,
                                  "modalities": tuple(sorted(set(modalities)))})


def role_reference(role: str) -> str:
    if not _ROLE.fullmatch(role):
        raise ValueError("invalid D45 event role")
    return _schema_reference("role", role)


def authorized_leaf_reference(kind: str, canonical: str) -> str:
    if not kind or not canonical:
        raise ValueError("authorized leaf reference requires kind and canonical value")
    return _reference("leaf", {"kind": kind, "canonical": canonical})


def event_property_reference(kind: str, value: str) -> str:
    if kind not in {"key", "value"} or not value:
        raise ValueError("event property reference is not canonical")
    return _schema_reference("property" if kind == "key" else "property-value", value)


@dataclass(frozen=True, order=True)
class D45Leaf:
    local_id: str
    kind: str
    surface: str
    canonical: str
    span: tuple[int, int]
    normalization_rule: str

    @property
    def semantic_ref(self) -> str:
        return _reference("leaf", {"kind": self.kind, "canonical": self.canonical})

    def verify(self, source: SemanticSource) -> bool:
        term = SemanticTerm(self.surface, self.canonical, self.kind,
                            self.span, self.normalization_rule)
        return bool(_IDENTIFIER.fullmatch(self.local_id) and
                    term.verify(source, allow_variable=False))


@dataclass(frozen=True, order=True)
class D45GrammarSymbol:
    local_id: str
    namespace: str
    symbol: str
    surface: str
    span: tuple[int, int]
    compiler_rule: str

    @property
    def semantic_ref(self) -> str:
        return _reference("symbol", {"namespace": self.namespace, "symbol": self.symbol})

    def verify(self, source: SemanticSource) -> bool:
        start, end = self.span
        return bool(
            _IDENTIFIER.fullmatch(self.local_id) and _IDENTIFIER.fullmatch(self.namespace) and
            self.symbol and _IDENTIFIER.fullmatch(self.compiler_rule) and
            0 <= start < end <= len(source.content) and
            source.content[start:end] == self.surface
        )


@dataclass(frozen=True)
class D45TermNode:
    local_id: str
    operator: str
    edges: tuple[tuple[str, str], ...]
    operator_symbol: str | None = None
    declared_key: str | None = None


@dataclass(frozen=True)
class D45Event:
    local_id: str
    predicate_ref: str
    roles: tuple[tuple[str, str], ...]
    polarity: str
    modalities: tuple[str, ...]
    temporal_modifiers: tuple[tuple[str, str], ...]
    event_properties: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class D45Readout:
    state: str
    value: object | None
    witnesses: tuple[tuple[str, tuple[int, int]], ...]
    reason: str


@dataclass(frozen=True)
class D45Graph:
    source: SemanticSource
    analysis_id: str
    alternative_set: str
    complete: bool
    environment_label: str
    leaves: tuple[D45Leaf, ...]
    symbols: tuple[D45GrammarSymbol, ...]
    nodes: tuple[D45TermNode, ...]
    events: tuple[D45Event, ...]
    semantic_refs: tuple[tuple[str, str], ...]
    node_payloads: tuple[tuple[str, str], ...]
    event_payloads: tuple[tuple[str, str], ...]

    def ref(self, local_id: str) -> str:
        values = dict(self.semantic_refs)
        if local_id not in values:
            raise KeyError(local_id)
        return values[local_id]

    @property
    def semantic_signature(self) -> tuple[str, ...]:
        payloads = dict(self.event_payloads)
        return tuple(sorted(payloads.values()))

    def _leaf_witnesses(self, local_id: str, trail: frozenset[str] = frozenset()) \
            -> tuple[tuple[str, tuple[int, int]], ...]:
        if local_id in trail:
            raise ValueError("cycle encountered during readout")
        leaves = {item.local_id: item for item in self.leaves}
        symbols = {item.local_id: item for item in self.symbols}
        if local_id in leaves:
            leaf = leaves[local_id]
            return ((leaf.local_id, leaf.span),)
        if local_id in symbols:
            symbol = symbols[local_id]
            return ((symbol.local_id, symbol.span),)
        nodes = {item.local_id: item for item in self.nodes}
        if local_id not in nodes:
            return ()
        witnesses = set()
        for _role, child in nodes[local_id].edges:
            witnesses.update(self._leaf_witnesses(child, trail | {local_id}))
        return tuple(sorted(witnesses))

    def readout(self, local_id: str, kind: str) -> D45Readout:
        leaves = {item.local_id: item for item in self.leaves}
        symbols = {item.local_id: item for item in self.symbols}
        nodes = {item.local_id: item for item in self.nodes}
        if kind == "leaf" and local_id in leaves:
            leaf = leaves[local_id]
            return D45Readout("resolved", leaf.canonical,
                              ((leaf.local_id, leaf.span),), "authorized leaf")
        if kind == "structured" and local_id in nodes:
            def expand(identifier: str) -> object:
                if identifier in leaves:
                    leaf = leaves[identifier]
                    return {"kind": leaf.kind, "value": leaf.canonical}
                if identifier in symbols:
                    symbol = symbols[identifier]
                    return {"kind": "grammar_symbol", "namespace": symbol.namespace,
                            "symbol": symbol.symbol}
                node = nodes[identifier]
                edges = sorted(((role, expand(child)) for role, child in node.edges),
                               key=lambda item: _canonical(item))
                return {"operator": node.operator, "edges": edges}
            return D45Readout("resolved", expand(local_id),
                              self._leaf_witnesses(local_id), "authorized structured term")
        return D45Readout("rejected", None, (),
                          "event, charge, symbol and digest references are not answer values")


@dataclass(frozen=True)
class D45Transport:
    scope: str
    sources: tuple[SealedSource, ...]
    facts: tuple[AuthorizedFact, ...]
    nogoods: tuple[frozenset[str], ...]
    graphs: tuple[D45Graph, ...]
    registry_attestations: tuple[tuple[int, str], ...]

    def executor(self) -> SigmaPBAExecutor:
        return SigmaPBAExecutor(sources=self.sources, facts=self.facts, scope=self.scope,
                                allowed_rules=frozenset({RULE}), nogoods=self.nogoods)


def _pair_rows(rows: Iterable[Iterable[object]]) -> tuple[tuple[str, str], ...]:
    result = tuple((str(row[0]), str(row[1])) for row in rows)
    if any(len(tuple(row)) != 2 for row in rows):
        raise ValueError("expected pairs")
    return result


class AuthorizedSemanticHypergraph:
    """Validate D45 declarations without performing semantic inference."""

    @staticmethod
    def _source(record: dict) -> SemanticSource:
        raw = record["source"]
        content = raw["content"]
        if len(content.encode()) > _MAX_SOURCE_BYTES:
            raise ValueError("D45 source byte budget exceeded")
        return SemanticSource.seal(raw["source_id"], content, raw["scope"])

    @classmethod
    def from_contract(cls, record: dict) -> D45Graph:
        source = cls._source(record)
        raw = record["analysis"]
        analysis_id = str(raw["analysis_id"])
        alternative_set = str(raw["alternative_set"])
        if not _IDENTIFIER.fullmatch(analysis_id) or not _IDENTIFIER.fullmatch(alternative_set):
            raise ValueError("invalid D45 analysis identity")
        if not raw.get("complete"):
            raise ValueError("incomplete analysis cannot authorize a D45 graph")

        leaves = tuple(D45Leaf(
            str(item["id"]), str(item["kind"]), str(item["surface"]),
            str(item["canonical"]), tuple(item["span"]), str(item["normalization_rule"])
        ) for item in raw.get("leaves", ()))
        symbols = tuple(D45GrammarSymbol(
            str(item["id"]), str(item["namespace"]), str(item["symbol"]),
            str(item["surface"]), tuple(item["span"]), str(item["compiler_rule"])
        ) for item in raw.get("symbols", ()))
        nodes = tuple(D45TermNode(
            str(item["id"]), str(item["operator"]), _pair_rows(item.get("edges", ())),
            item.get("operator_symbol"), item.get("declared_key")
        ) for item in raw.get("nodes", ()))
        events = tuple(D45Event(
            str(item["id"]), str(item["predicate_ref"]),
            _pair_rows(item.get("roles", ())), str(item["polarity"]),
            tuple(str(value) for value in item.get("modalities", ())),
            _pair_rows(item.get("temporal_modifiers", ())),
            _pair_rows(item.get("event_properties", ())),
        ) for item in raw.get("events", ()))
        if not (symbols and nodes and events):
            raise ValueError("D45 graph requires symbols, nodes and events")
        if len(leaves) > _MAX_LEAVES or len(symbols) > _MAX_SYMBOLS or \
                len(nodes) > _MAX_NODES or len(events) > _MAX_EVENTS:
            raise ValueError("D45 graph object budget exceeded")
        all_ids = [item.local_id for collection in (leaves, symbols, nodes, events)
                   for item in collection]
        if len(set(all_ids)) != len(all_ids) or any(not _IDENTIFIER.fullmatch(item) for item in all_ids):
            raise ValueError("D45 local identities must be unique and canonical")
        if not all(item.verify(source) for item in leaves) or \
                not all(item.verify(source) for item in symbols):
            raise ValueError("D45 terminal failed exact authorization")

        references = {item.local_id: item.semantic_ref for item in leaves}
        references.update({item.local_id: item.semantic_ref for item in symbols})
        node_by_id = {item.local_id: item for item in nodes}
        symbol_ids = {item.local_id for item in symbols}
        edge_count = sum(len(item.edges) for item in nodes) + sum(
            len(item.roles) + len(item.modalities) + len(item.temporal_modifiers)
            + len(item.event_properties) for item in events)
        if edge_count > _MAX_EDGES:
            raise ValueError("D45 edge budget exceeded")

        node_payloads: dict[str, str] = {}
        used_terminals: set[str] = set()

        def compute_node(local_id: str, trail: tuple[str, ...] = ()) -> str:
            if local_id in references:
                if local_id not in node_by_id:
                    used_terminals.add(local_id)
                return references[local_id]
            if local_id not in node_by_id:
                raise ValueError("dangling D45 term reference")
            if local_id in trail:
                raise ValueError("D45 term graph contains a cycle")
            if len(trail) >= _MAX_DEPTH:
                raise ValueError("D45 graph depth budget exceeded")
            node = node_by_id[local_id]
            if node.operator not in _OPERATORS or not node.edges:
                raise ValueError("unknown or empty D45 term operator")
            roles = tuple(role for role, _child in node.edges)
            if any(role not in _OPERATORS[node.operator] for role in roles):
                raise ValueError("operator received an invalid typed edge")
            if node.operator in _COMMUTATIVE:
                if len(node.edges) < 2 or set(roles) != {"member"}:
                    raise ValueError("coordination requires two or more members")
            elif node.operator == "compound" and sorted(roles) != ["head", "modifier"]:
                raise ValueError("compound requires one head and one modifier")
            elif node.operator == "quantified" and sorted(roles) != ["entity", "quantifier"]:
                raise ValueError("quantified requires entity and quantifier")
            elif node.operator == "entity_bundle" and len(set(node.edges)) != len(node.edges):
                raise ValueError("entity bundle cannot duplicate an edge")
            edges = [(role, compute_node(child, trail + (local_id,)))
                     for role, child in node.edges]
            edges.sort()
            operator_ref = None
            if node.operator_symbol is not None:
                if node.operator_symbol not in symbol_ids:
                    raise ValueError("node operator symbol is missing")
                used_terminals.add(node.operator_symbol)
                operator_ref = references[node.operator_symbol]
            payload = {"operator": node.operator, "operator_ref": operator_ref, "edges": edges}
            semantic_ref = structured_term_reference(node.operator, edges, operator_ref)
            if node.declared_key is not None:
                if not _SHA256.fullmatch(str(node.declared_key)) or \
                        str(node.declared_key) != semantic_ref.rsplit(":", 1)[1]:
                    raise ValueError("forged D45 node key")
            references[local_id] = semantic_ref
            node_payloads[local_id] = _canonical(payload)
            return semantic_ref

        for node in sorted(nodes, key=lambda item: item.local_id):
            compute_node(node.local_id)

        event_payloads: dict[str, str] = {}
        for event in events:
            if event.predicate_ref not in symbol_ids:
                raise ValueError("event predicate symbol is missing")
            if event.polarity not in _POLARITIES or not event.roles or \
                    len({role for role, _ in event.roles}) != len(event.roles) or \
                    any(not _ROLE.fullmatch(role) for role, _ in event.roles):
                raise ValueError("event roles or polarity are invalid")
            used_terminals.add(event.predicate_ref)
            roles = tuple(sorted((role, compute_node(term)) for role, term in event.roles))
            modal_refs = []
            for symbol in event.modalities:
                if symbol not in symbol_ids:
                    raise ValueError("event modality symbol is missing")
                used_terminals.add(symbol)
                modal_refs.append(references[symbol])
            temporal = []
            for relation, term in event.temporal_modifiers:
                if relation not in symbol_ids:
                    raise ValueError("temporal relation symbol is missing")
                used_terminals.add(relation)
                temporal.append((references[relation], compute_node(term)))
            if event.event_properties != tuple(sorted(set(event.event_properties))):
                raise ValueError("event properties must be unique and sorted")
            charge_payload = {"polarity": event.polarity,
                              "modalities": tuple(sorted(set(modal_refs)))}
            payload = {
                "predicate_ref": references[event.predicate_ref],
                "roles": roles,
                "charge_ref": charge_reference(event.polarity, modal_refs),
                "temporal_modifiers": tuple(sorted(temporal)),
                "event_properties": event.event_properties,
            }
            references[event.local_id] = _reference("event", payload)
            event_payloads[event.local_id] = _canonical(payload)

        reachable_nodes: set[str] = set()
        reachable_terminals: set[str] = set()

        def mark(identifier: str) -> None:
            if identifier in reachable_terminals or identifier in reachable_nodes:
                return
            if identifier in node_by_id:
                reachable_nodes.add(identifier)
                node = node_by_id[identifier]
                if node.operator_symbol is not None:
                    reachable_terminals.add(node.operator_symbol)
                for _role, child in node.edges:
                    mark(child)
            elif identifier in references:
                reachable_terminals.add(identifier)
            else:
                raise ValueError("reachable D45 reference is dangling")

        for event in events:
            reachable_terminals.add(event.predicate_ref)
            reachable_terminals.update(event.modalities)
            for _role, term in event.roles:
                mark(term)
            for relation, term in event.temporal_modifiers:
                reachable_terminals.add(relation)
                mark(term)
        all_terminals = {item.local_id for item in leaves + symbols}
        if all_terminals != reachable_terminals or set(node_by_id) != reachable_nodes:
            raise ValueError("D45 graph contains an orphan authorized terminal or node")
        environment = f"analysis:{source.source_id}:{alternative_set}:{analysis_id}"
        return D45Graph(
            source, analysis_id, alternative_set, True, environment,
            tuple(sorted(leaves)), tuple(sorted(symbols)),
            tuple(sorted(nodes, key=lambda item: item.local_id)),
            tuple(sorted(events, key=lambda item: item.local_id)),
            tuple(sorted(references.items())), tuple(sorted(node_payloads.items())),
            tuple(sorted(event_payloads.items())),
        )

    @staticmethod
    def _event_span(graph: D45Graph, event: D45Event) -> tuple[int, int]:
        leaf_by_id = {item.local_id: item for item in graph.leaves}
        symbol_by_id = {item.local_id: item for item in graph.symbols}
        node_by_id = {item.local_id: item for item in graph.nodes}
        spans = [symbol_by_id[event.predicate_ref].span]
        seen: set[str] = set()

        def collect(identifier: str) -> None:
            if identifier in seen:
                return
            seen.add(identifier)
            if identifier in leaf_by_id:
                spans.append(leaf_by_id[identifier].span)
            elif identifier in node_by_id:
                node = node_by_id[identifier]
                if node.operator_symbol:
                    spans.append(symbol_by_id[node.operator_symbol].span)
                for _role, child in node.edges:
                    collect(child)
        for _role, term in event.roles:
            collect(term)
        for modal in event.modalities:
            spans.append(symbol_by_id[modal].span)
        for relation, term in event.temporal_modifiers:
            spans.append(symbol_by_id[relation].span)
            collect(term)
        return min(item[0] for item in spans), max(item[1] for item in spans)

    @classmethod
    def transport(cls, graphs: tuple[D45Graph, ...]) -> D45Transport:
        if not graphs or len(graphs) > 256:
            raise ValueError("D45 transport requires 1-256 graphs")
        scopes = {item.source.scope for item in graphs}
        if len(scopes) != 1:
            raise ValueError("D45 transport cannot mix scopes")
        sources_by_id: dict[str, SemanticSource] = {}
        alternatives: dict[tuple[str, str], set[str]] = {}
        rows = []
        for graph in graphs:
            prior = sources_by_id.get(graph.source.source_id)
            if prior is not None and prior != graph.source:
                raise ValueError("D45 source identity collision")
            sources_by_id[graph.source.source_id] = graph.source
            alternatives.setdefault((graph.source.source_id, graph.alternative_set), set()).add(
                graph.environment_label)
            refs = dict(graph.semantic_refs)
            for event in graph.events:
                event_ref = refs[event.local_id]
                event_span = cls._event_span(graph, event)
                predicate_ref = refs[event.predicate_ref]
                charge_payload = {"polarity": event.polarity,
                                  "modalities": tuple(sorted(refs[item]
                                                              for item in event.modalities))}
                charge_ref = charge_reference(event.polarity, charge_payload["modalities"])
                components = [
                    ("d45_event_predicate", (event_ref, predicate_ref)),
                    ("d45_event_charge", (event_ref, charge_ref)),
                ]
                components.extend(("d45_event_role",
                                   (event_ref, role_reference(role), refs[term]))
                                  for role, term in event.roles)
                components.extend(("d45_event_temporal",
                                   (event_ref, refs[relation], refs[term]))
                                  for relation, term in event.temporal_modifiers)
                components.extend(("d45_event_property",
                                   (event_ref, _schema_reference("property", key),
                                    _schema_reference("property-value", value)))
                                  for key, value in event.event_properties)
                for predicate, arguments in components:
                    payload = {
                        "analysis": graph.analysis_id, "environment": graph.environment_label,
                        "event": dict(graph.event_payloads)[event.local_id],
                        "predicate": predicate, "arguments": arguments,
                        "source_id": graph.source.source_id,
                    }
                    rows.append((_canonical(payload), graph, predicate, arguments,
                                 event_span, _digest(b"horizon-d45-registry-attestation-v1", payload)))
        rows.sort(key=lambda item: item[0])
        facts = []
        attestations = []
        for fact_id, (_canonical_payload, graph, predicate, arguments, span, attestation) \
                in enumerate(rows, 1):
            fact = AuthorizedFact.seal(
                fact_id=fact_id, predicate=predicate, arguments=arguments,
                scope=graph.source.scope, source=graph.source.as_sigma_source(),
                source_span=span, compiler_rule=RULE,
                orbit=f"d45:{attestation}", assumptions=(graph.environment_label,),
            )
            facts.append(fact)
            attestations.append((fact_id, attestation))
        nogoods = []
        for labels in alternatives.values():
            ordered = sorted(labels)
            nogoods.extend(frozenset((left, right)) for index, left in enumerate(ordered)
                           for right in ordered[index + 1:])
        sigma_sources = tuple(source.as_sigma_source()
                              for source in sorted(sources_by_id.values(), key=lambda item: item.source_id))
        return D45Transport(next(iter(scopes)), sigma_sources, tuple(facts),
                            tuple(sorted(set(nogoods), key=lambda item: tuple(sorted(item)))),
                            tuple(sorted(graphs, key=lambda item: item.environment_label)),
                            tuple(attestations))


__all__ = [
    "AuthorizedSemanticHypergraph", "D45Event", "D45GrammarSymbol", "D45Graph",
    "D45Leaf", "D45Readout", "D45TermNode", "D45Transport", "RULE",
    "authorized_leaf_reference", "charge_reference", "event_property_reference",
    "role_reference", "structured_term_reference",
]

# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic JSON-Pointer adapter with exact byte-character source spans."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .typed_causal_ingest import (
    CausalSourceEnvelope, DeterministicCausalCompiler, StructuredCausalDeclaration,
)
from .typed_causal_program import TypedCausalFact
from .causal_adapter_protocol import CausalAdapterBatch


_TOKEN = re.compile(
    r'(?P<ws>\s+)|(?P<string>"(?:\\["\\/bfnrt]|\\u[0-9a-fA-F]{4}|[^"\\])*")|'
    r'(?P<number>-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)|'
    r'(?P<literal>true|false|null)|(?P<punct>[{}\[\],:])'
)


@dataclass(frozen=True)
class JsonLeaf:
    pointer: str
    value: object
    source_value: str
    span: tuple[int, int]


class JsonSourceMap:
    """Parse strict JSON once and retain an exact span for every scalar leaf."""

    def __init__(self, content: str):
        self.content = content
        self.tokens = tuple(self._tokenize(content))
        self.position = 0
        self.leaves: dict[str, JsonLeaf] = {}
        self._parse_value("")
        if self.position != len(self.tokens):
            raise ValueError("trailing JSON tokens")

    @staticmethod
    def _tokenize(content: str):
        position = 0
        while position < len(content):
            match = _TOKEN.match(content, position)
            if match is None:
                raise ValueError(f"invalid JSON token at offset {position}")
            position = match.end()
            if match.lastgroup != "ws":
                yield match.lastgroup, match.group(0), match.start(), match.end()

    def _peek(self):
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def _take(self, kind=None, surface=None):
        token = self._peek()
        if token is None or (kind is not None and token[0] != kind) or \
                (surface is not None and token[1] != surface):
            raise ValueError(f"unexpected JSON token: {token}")
        self.position += 1
        return token

    @staticmethod
    def _child(pointer: str, component: str) -> str:
        escaped = component.replace("~", "~0").replace("/", "~1")
        return f"{pointer}/{escaped}"

    def _parse_value(self, pointer: str) -> None:
        token = self._peek()
        if token is None:
            raise ValueError("truncated JSON")
        if token[1] == "{":
            self._take("punct", "{")
            keys = set()
            if self._peek() and self._peek()[1] != "}":
                while True:
                    key_token = self._take("string")
                    try:
                        key = json.loads(key_token[1])
                    except json.JSONDecodeError as error:
                        raise ValueError("invalid JSON object key") from error
                    if key in keys:
                        raise ValueError("duplicate JSON object key")
                    keys.add(key)
                    self._take("punct", ":")
                    self._parse_value(self._child(pointer, key))
                    if self._peek() and self._peek()[1] == ",":
                        self._take("punct", ",")
                        continue
                    break
            self._take("punct", "}")
            return
        if token[1] == "[":
            self._take("punct", "[")
            index = 0
            if self._peek() and self._peek()[1] != "]":
                while True:
                    self._parse_value(self._child(pointer, str(index)))
                    index += 1
                    if self._peek() and self._peek()[1] == ",":
                        self._take("punct", ",")
                        continue
                    break
            self._take("punct", "]")
            return
        kind, surface, start, end = self._take()
        if kind not in ("string", "number", "literal"):
            raise ValueError("JSON scalar expected")
        try:
            value = json.loads(surface)
        except json.JSONDecodeError as error:
            raise ValueError("invalid JSON scalar") from error
        if kind == "string":
            encoded = surface[1:-1]
            if encoded != value:
                raise ValueError("escaped JSON strings are not admissible causal microcitations")
            start, end, source_value = start + 1, end - 1, value
        else:
            source_value = surface
        self.leaves[pointer] = JsonLeaf(pointer, value, source_value, (start, end))

    def leaf(self, pointer: str) -> JsonLeaf:
        try:
            return self.leaves[pointer]
        except KeyError as error:
            raise ValueError(f"JSON pointer does not select a scalar leaf: {pointer}") from error


@dataclass(frozen=True)
class JsonCausalMapping:
    fact_id: int
    value_pointer: str
    subject: str
    predicate: str
    observed_at: int
    event_time: int
    unit_pointer: str | None = None
    version: int = 1
    polarity: int = 1
    asserted: bool = True
    event_id: str = ""
    causes: tuple[int, ...] = ()


class JsonPointerCausalAdapter:
    adapter_id = "json-pointer-v1"

    @staticmethod
    def compile(source_id: str, content: str, scope: str,
                mappings: tuple[JsonCausalMapping, ...]) -> tuple[TypedCausalFact, ...]:
        if not mappings or tuple(item.fact_id for item in mappings) != \
                tuple(sorted({item.fact_id for item in mappings})):
            raise ValueError("JSON causal mappings must be non-empty and FactId-canonical")
        source = CausalSourceEnvelope.seal(source_id, content)
        source_map = JsonSourceMap(content)
        facts = []
        for mapping in mappings:
            leaf = source_map.leaf(mapping.value_pointer)
            unit = ""
            if mapping.unit_pointer is not None:
                unit_leaf = source_map.leaf(mapping.unit_pointer)
                if not isinstance(unit_leaf.value, str) or not unit_leaf.value:
                    raise ValueError("JSON causal unit pointer must select a non-empty string")
                unit = unit_leaf.value
            declaration = StructuredCausalDeclaration(
                mapping.fact_id, scope, mapping.subject, mapping.predicate,
                leaf.source_value, leaf.span, mapping.observed_at, mapping.event_time,
                mapping.version, unit, mapping.polarity, mapping.asserted,
                mapping.event_id, mapping.causes)
            facts.append(DeterministicCausalCompiler.compile(source, declaration))
        return tuple(facts)

    @classmethod
    def compile_batch(cls, batch: CausalAdapterBatch) -> tuple[TypedCausalFact, ...]:
        if any(not isinstance(item, JsonCausalMapping) for item in batch.declarations):
            raise TypeError("JSON adapter declarations must be JsonCausalMapping values")
        return cls.compile(batch.source_id, batch.content, batch.scope,
                           tuple(batch.declarations))

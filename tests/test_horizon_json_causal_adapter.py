# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.json_causal_adapter import (
    JsonCausalMapping, JsonPointerCausalAdapter, JsonSourceMap,
)


def test_json_pointer_maps_number_and_sibling_unit_to_exact_provenance():
    content = '{"gpu":[{"temperature":{"value":42,"unit":"C"}}]}'
    facts = JsonPointerCausalAdapter.compile("sample", content, "scope", (
        JsonCausalMapping(1, "/gpu/0/temperature/value", "gpu0", "temperature", 7, 7,
                          "/gpu/0/temperature/unit"),))
    assert facts[0].value == "42"
    assert facts[0].unit == "C"
    assert content[slice(*facts[0].source_span)] == "42"


def test_json_pointer_escaping_and_arrays_are_rfc6901_compatible():
    source = JsonSourceMap('{"a/b":{"~key":[true,null]}}')
    assert source.leaf("/a~1b/~0key/0").source_value == "true"
    assert source.leaf("/a~1b/~0key/1").source_value == "null"


def test_duplicate_keys_and_escaped_string_microcitations_fail_closed():
    with pytest.raises(ValueError, match="duplicate"):
        JsonSourceMap('{"x":1,"x":2}')
    with pytest.raises(ValueError, match="escaped JSON strings"):
        JsonSourceMap('{"x":"line\\nfeed"}')


def test_missing_pointer_or_non_string_unit_fails_closed():
    content = '{"metric":{"value":3,"unit":7}}'
    with pytest.raises(ValueError, match="scalar leaf"):
        JsonPointerCausalAdapter.compile("sample", content, "scope", (
            JsonCausalMapping(1, "/missing", "gpu0", "metric", 1, 1),))
    with pytest.raises(ValueError, match="unit pointer"):
        JsonPointerCausalAdapter.compile("sample", content, "scope", (
            JsonCausalMapping(1, "/metric/value", "gpu0", "metric", 1, 1,
                              "/metric/unit"),))

# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.causal_adapter_protocol import CausalAdapterBatch
from horizon_memory.regex_log_adapter import (
    RegexLogCausalAdapter,
    RegexLogCausalMapping,
)
from horizon_memory.standalone_causal_memory import StandaloneCausalMemory
from horizon_memory.typed_causal_program import (
    CausalSelector,
    TypedCausalProgram,
)


LOG = (
    "2026-01-02 03:04:05,006 [INFO] runtime: GPU ready | CUs: 32 | VRAM: 8176.0 MB\n"
    "2026-01-02 03:05:06,007 [INFO] runtime: GPU ready | CUs: 40 | VRAM: 9000.0 MB\n"
)


def _mappings():
    return (
        RegexLogCausalMapping(1, r"CUs: (?P<value>\d+)", "value", "gpu0", "compute_units"),
        RegexLogCausalMapping(100, r"VRAM: (?P<value>\d+(?:\.\d+)?) MB", "value",
                              "gpu0", "vram", "MB"),
    )


def test_log_values_get_exact_spans_and_timestamp_clocks():
    facts = RegexLogCausalAdapter.compile("runtime.log", LOG, "scope", _mappings())
    assert [item.value for item in facts] == ["32", "40", "8176.0", "9000.0"]
    assert facts[0].event_time < facts[1].event_time
    for fact in facts:
        assert LOG[fact.source_span[0]:fact.source_span[1]] == fact.value


def test_real_standalone_boundary_reopens_every_log_proof():
    batch = CausalAdapterBatch("runtime.log", LOG, "scope", _mappings())
    memory = StandaloneCausalMemory("scope")
    receipt = memory.ingest(RegexLogCausalAdapter(), batch)
    assert receipt.state == "APPLIED"
    facts = RegexLogCausalAdapter.compile_batch(batch)
    assert receipt.fact_ids == tuple(item.fact_id for item in facts)
    result = memory.query(TypedCausalProgram("LOOKUP", CausalSelector("gpu0", "vram")))
    assert (result.state, result.value, result.unit) == ("resolved", "9000.0", "MB")
    assert len(result.proofs) == 1 and memory.verify_proof(result.proofs[0])


def test_missing_timestamp_and_factid_overlap_fail_closed():
    mapping = RegexLogCausalMapping(1, r"CUs: (?P<value>\d+)", "value", "gpu0", "cu")
    try:
        RegexLogCausalAdapter.compile("bad", "CUs: 32\n", "scope", (mapping,))
    except ValueError as error:
        assert "timestamp" in str(error)
    else:
        raise AssertionError("timestamp-free causal log was accepted")
    overlap = RegexLogCausalMapping(1, r"VRAM: (?P<value>\d+(?:\.\d+)?)", "value",
                                    "gpu0", "vram")
    try:
        RegexLogCausalAdapter.compile("bad", LOG, "scope", (mapping, overlap))
    except ValueError as error:
        assert "overlap" in str(error)
    else:
        raise AssertionError("overlapping FactId ranges were accepted")

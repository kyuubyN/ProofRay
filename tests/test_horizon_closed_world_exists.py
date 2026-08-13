# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import json

from horizon_memory.causal_adapter_protocol import CausalAdapterBatch
from horizon_memory.closed_world_exists import (
    ClosedWorldExistsEngine,
    ClosedWorldFiberCertificate,
)
from horizon_memory.json_causal_adapter import JsonCausalMapping, JsonPointerCausalAdapter
from horizon_memory.standalone_causal_memory import StandaloneCausalMemory


def _engine(include_beta=True):
    values = ["telescope alpha"] + (["telescope beta"] if include_beta else []) + ["repair"]
    content = json.dumps({"values": values}, separators=(",", ":"))
    mappings = [JsonCausalMapping(1, "/values/0", "Mina", "repair", 1, 1,
                                  event_id="alpha")]
    seal_id = 3 if include_beta else 2
    if include_beta:
        mappings.append(JsonCausalMapping(2, "/values/1", "Mina", "repair", 2, 2,
                                          event_id="beta"))
    mappings.append(JsonCausalMapping(seal_id, f"/values/{len(values)-1}", "Mina",
                                      "__complete__", 3, 3, event_id="repair-seal"))
    batch = CausalAdapterBatch("exists-source", content, "scope", tuple(mappings))
    adapter = JsonPointerCausalAdapter(); facts = adapter.compile_batch(batch)
    memory = StandaloneCausalMemory("scope")
    assert memory.ingest(adapter, batch).state == "APPLIED"
    return ClosedWorldExistsEngine(memory, facts, (
        ClosedWorldFiberCertificate("Mina", "repair", seal_id),))


def test_positive_existence_carries_witness_and_completeness_proof():
    result = _engine().query("Did Mina repair telescope alpha?")
    assert (result.state, result.value) == ("resolved", True)
    assert len(result.proofs) == 2


def test_negative_existence_is_proved_by_seal_not_silence():
    result = _engine().query("Did Mina repair telescope gamma?")
    assert (result.state, result.value) == ("resolved", False)
    assert len(result.proofs) == 1


def test_missing_or_wrong_fiber_abstains_instead_of_fabricating_false():
    result = _engine().query("Did Jon repair telescope alpha?")
    assert result.state == "abstain"
    assert result.value is None


def test_uncertain_latest_witness_does_not_become_true_or_false():
    content = json.dumps({"values": ["telescope alpha", "repair"]}, separators=(",", ":"))
    mappings = (JsonCausalMapping(1, "/values/0", "Mina", "repair", 1, 1,
                                  asserted=False, event_id="alpha"),
                JsonCausalMapping(2, "/values/1", "Mina", "__complete__", 2, 2,
                                  event_id="seal"))
    batch = CausalAdapterBatch("uncertain-source", content, "scope", mappings)
    adapter = JsonPointerCausalAdapter(); facts = adapter.compile_batch(batch)
    memory = StandaloneCausalMemory("scope"); assert memory.ingest(adapter, batch).state == "APPLIED"
    result = ClosedWorldExistsEngine(memory, facts, (
        ClosedWorldFiberCertificate("Mina", "repair", 2),)).query(
            "Did Mina repair telescope alpha?")
    assert result.state == "abstain"

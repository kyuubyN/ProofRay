# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import multiprocessing
import stat

from horizon_memory.causal_adapter_protocol import CausalAdapterBatch
from horizon_memory.concurrent_durable_memory import ConcurrentDurableCausalMemory
from horizon_memory.json_causal_adapter import (
    JsonCausalMapping, JsonPointerCausalAdapter,
)
from horizon_memory.typed_causal_program import (
    CausalSelector, TypedCausalProgram,
)


def _batch(fact_id, value=None, source=None):
    value = fact_id if value is None else value
    content = json.dumps({"value": value}, separators=(",", ":"))
    mapping = JsonCausalMapping(
        fact_id, "/value", "workers", "value", fact_id, fact_id,
        event_id=f"event:{fact_id}")
    return CausalAdapterBatch(source or f"worker:{fact_id}", content, "scope", (mapping,))


def _worker(ledger, fact_ids, queue):
    memory = ConcurrentDurableCausalMemory("scope", ledger)
    adapter = JsonPointerCausalAdapter()
    queue.put(tuple(memory.ingest(adapter, _batch(fact_id)).state for fact_id in fact_ids))


def _collision_worker(ledger, value, queue):
    memory = ConcurrentDurableCausalMemory("scope", ledger)
    receipt = memory.ingest(JsonPointerCausalAdapter(), _batch(
        1, value=value, source=f"collision:{value}"))
    queue.put(receipt.state)


def test_multiple_processes_commit_without_lost_updates(tmp_path):
    ledger = tmp_path / "concurrent.jsonl"
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    workers = [context.Process(
        target=_worker, args=(ledger, tuple(range(worker * 10 + 1, worker * 10 + 11)), queue))
        for worker in range(4)]
    for process in workers:
        process.start()
    for process in workers:
        process.join(20)
        assert process.exitcode == 0
    states = tuple(state for _ in workers for state in queue.get(timeout=2))
    assert states == ("APPLIED",) * 40
    memory = ConcurrentDurableCausalMemory("scope", ledger)
    assert memory.record_count == memory.fact_count == 40
    result = memory.query(TypedCausalProgram(
        "COUNT_DISTINCT", CausalSelector("workers", "value"), closed_world=True))
    assert result.state == "resolved" and result.value == "40"
    assert stat.S_IMODE(memory.lock_path.stat().st_mode) == 0o600


def test_concurrent_fact_id_collision_has_one_winner_and_no_lost_record(tmp_path):
    ledger = tmp_path / "collision.jsonl"
    context = multiprocessing.get_context("fork")
    queue = context.Queue()
    workers = [context.Process(target=_collision_worker, args=(ledger, value, queue))
               for value in (10, 20, 30, 40)]
    for process in workers:
        process.start()
    for process in workers:
        process.join(20)
        assert process.exitcode == 0
    states = sorted(queue.get(timeout=2) for _ in workers)
    assert states.count("APPLIED") == 1 and states.count("REJECTED_COLLISION") == 3
    memory = ConcurrentDurableCausalMemory("scope", ledger)
    assert memory.record_count == memory.fact_count == 1
    result = memory.query(TypedCausalProgram("LOOKUP", CausalSelector("workers", "value")))
    assert result.state == "resolved" and result.value in {"10", "20", "30", "40"}

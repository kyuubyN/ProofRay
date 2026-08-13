# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checker de LINEARIZABILIDADE puro da Horizon Memory (V23-D0 / FH-02).

Autoridade única de verificação de concorrência: dada uma HISTÓRIA (invocações/respostas de operações de
vários atores), decide se existe uma ORDEM TOTAL das operações que (a) respeita a ordem de tempo real
`response(A) < invocation(B) ⟹ A antes de B` e (b) reproduz exatamente cada resposta observada quando
aplicada a um MODELO SEQUENCIAL de referência. É bounded model checking (busca topológica com pruning),
não prova formal geral.

Identidade: `InvocationId` (uma chamada física única) é SEPARADO do `operation_id` semântico — dois
`InvocationId` diferentes podem carregar o MESMO `operation_id` (retry), e o modelo os deduplica.

Semântica congelada (FH-02 §5):
  - PUT/DELETE publicado lineariza na troca atômica do `CURRENT`; o ACK vem depois da durabilidade;
  - READ lineariza no ponto onde lê o estado (a ReadView que usou);
  - ROTATION lineariza na publicação do novo `CURRENT` mas NÃO muda estado lógico (mesma geração);
  - COMPACTION lineariza na publicação e cria a geração seguinte, preservando o estado lógico;
  - RECOVERY sem mudança lógica NÃO cria versão nova (só restaura capacidade de escrita);
  - DELETE é terminal dentro da geração observada.

Operações INCOMPLETAS (sem resposta) podem ser OMITIDAS ou linearizadas se o efeito ficou observável.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field

# tipos de operação
PUT, DELETE, READ = "PUT", "DELETE", "READ"
CAPTURE, ROTATE, COMPACT, RECOVER = "CAPTURE", "ROTATE", "COMPACT", "RECOVER"
_WRITES = (PUT, DELETE)

# status de resultado (espelham o runtime)
APPLIED, IDEMPOTENT, STALE_REJECTED, VERSION_CONFLICT = "APPLIED", "IDEMPOTENT", "STALE_REJECTED", "VERSION_CONFLICT"
DEDUP_REPLAY, TXID_CONFLICT = "DEDUP_REPLAY", "TXID_CONFLICT"
PRESENT, DELETED, NOT_FOUND = "present", "deleted", "not_found"


@dataclass(frozen=True)
class Op:
    """Uma operação observada. `inv_id` é o `InvocationId` (chamada física única); `operation_id` é o id
    semântico de dedup (retries compartilham). `invocation`/`response` são tempos de tempo real (inteiros
    monotônicos); `response=None` = incompleta. `result` é a resposta OBSERVADA (None se incompleta)."""
    inv_id: int
    actor: str
    kind: str
    invocation: int
    response: int | None
    operation_id: int | None = None
    fact_id: int | None = None
    version: int | None = None
    value: int | None = None
    result: object = None          # tupla normalizada por kind, ou None se incompleta
    snapshot_of: int | None = None  # para READ: o inv_id do CAPTURE cuja ReadView foi usada (ou None)


def _preflight(cur, version, value, kind):
    """Regra de versão do runtime (sem mutação). `cur` = (version, kind, value) ou None."""
    if cur is None:
        return APPLIED
    cv, ck, cval = cur
    if version < cv:
        return STALE_REJECTED
    if version == cv:
        return IDEMPOTENT if (kind, value) == (ck, cval) else VERSION_CONFLICT
    return APPLIED


# resultado de `apply_op` (FH-02.1 §2): três desfechos distintos
VALID = "VALID"              # transição válida — `expected` é a resposta que o runtime deveria dar
INVALID = "INVALID"          # operação INVÁLIDA no modelo (ex.: READ com snapshot_of que nunca existiu)
NOT_READY = "NOT_READY"      # dependência de dados ainda não linearizada (ex.: CAPTURE pendente) — tentar depois


def command_digest(op: Op):
    """Digest CANÔNICO do COMANDO (não do payload): dois `InvocationId` com o mesmo `operation_id` que
    carreguem o MESMO comando são retry (→ DEDUP_REPLAY); comandos DIFERENTES no mesmo `operation_id` são
    conflito (→ TXID_CONFLICT). `value` já é um TOKEN opaco vindo do recorder (FH-02.1 §7)."""
    return (op.kind, op.fact_id, op.version, op.value)


@dataclass
class _State:
    facts: dict = field(default_factory=dict)     # fid -> (version, kind, value)
    dedup: dict = field(default_factory=dict)     # operation_id -> (command_digest, status, seq)
    read_seq: int = 0
    generation: int = 0
    captures: dict = field(default_factory=dict)  # capture inv_id -> frozen (facts_tuple, read_seq, gen)

    def key(self):
        return (tuple(sorted((f, v) for f, v in self.facts.items())),
                tuple(sorted(self.dedup.items())), self.read_seq, self.generation,
                tuple(sorted(self.captures.items())))

    def copy(self):
        return _State(dict(self.facts), dict(self.dedup), self.read_seq, self.generation,
                      dict(self.captures))


def _facts_snapshot(state):
    return tuple(sorted((f, v) for f, v in state.facts.items()))


def apply_op(state: _State, op: Op, capture_ids=None):
    """Aplica `op` ao modelo sequencial. Devolve `(outcome, expected)` (FH-02.1 §2):
      VALID     — transição legal; `expected` é a resposta que o runtime deveria dar (muta `state`);
      NOT_READY — dependência de dados pendente (ex.: o CAPTURE alvo ainda não linearizou) — NÃO muta;
      INVALID   — operação estruturalmente inválida no modelo (ex.: `snapshot_of` que nunca foi um
                  CAPTURE) — NÃO muta, NUNCA cai para leitura atual.
    `capture_ids` é o conjunto de `inv_id` que SÃO CAPTUREs na história (para distinguir NOT_READY de
    INVALID)."""
    cap_ids = capture_ids if capture_ids is not None else set()
    if op.kind in _WRITES:
        if op.operation_id is not None and op.operation_id in state.dedup:
            stored_digest, orig_status, orig_seq = state.dedup[op.operation_id]
            if command_digest(op) == stored_digest:                 # MESMO comando → replay
                return (VALID, (DEDUP_REPLAY, orig_seq) if orig_status == APPLIED else (orig_status, None))
            return (VALID, (TXID_CONFLICT, None))                   # comando DIFERENTE → conflito
        cur = state.facts.get(op.fact_id)
        decision = _preflight(cur, op.version, op.value, op.kind)
        if decision == APPLIED:
            state.read_seq += 1
            seq = state.read_seq
            state.facts[op.fact_id] = (op.version, op.kind, None if op.kind == DELETE else op.value)
            if op.operation_id is not None:
                state.dedup[op.operation_id] = (command_digest(op), APPLIED, seq)
            return (VALID, (APPLIED, seq))
        # Política congelada do WAL: dedup durável SOMENTE para APPLIED. STALE/IDEMPOTENT/CONFLICT não
        # geram frame nem entram no TxIdIndex; um retry posterior precisa ser recalculado contra a versão
        # então vigente (e pode, por exemplo, mudar de IDEMPOTENT para STALE após uma versão maior).
        return (VALID, (decision, None))
    if op.kind == READ:
        if op.snapshot_of is not None:
            if op.snapshot_of not in cap_ids:
                return (INVALID, None)                              # snapshot inexistente → INVÁLIDO (§2)
            if op.snapshot_of not in state.captures:
                return (NOT_READY, None)                            # CAPTURE alvo ainda não linearizou
            facts_tuple, _rs, _gen = state.captures[op.snapshot_of]
            cur = dict(facts_tuple).get(op.fact_id)
        else:
            cur = state.facts.get(op.fact_id)
        if cur is None:
            return (VALID, (NOT_FOUND, None))
        ver, k, val = cur
        return (VALID, (DELETED, None) if k == DELETE else (PRESENT, val))
    if op.kind == CAPTURE:
        state.captures[op.inv_id] = (_facts_snapshot(state), state.read_seq, state.generation)
        return (VALID, (state.generation, state.read_seq))
    if op.kind == ROTATE:
        return (VALID, (state.generation,))          # mesmo generation; sem mudança de estado lógico
    if op.kind == COMPACT:
        state.generation += 1                        # nova geração; estado lógico preservado
        return (VALID, (state.generation,))
    if op.kind == RECOVER:
        return (VALID, ("recovered",))               # sem mudança lógica, sem versão nova
    return (INVALID, None)


# veredito do checker (FH-02.1 §3): três estados distintos — orçamento esgotado NUNCA vira NON_LINEARIZABLE
LINEARIZABLE = "LINEARIZABLE"
NON_LINEARIZABLE = "NON_LINEARIZABLE"
BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


@dataclass(frozen=True)
class CheckResult:
    verdict: str                                        # LINEARIZABLE | NON_LINEARIZABLE | BUDGET_EXHAUSTED
    reason: str
    counterexample: list = field(default_factory=list)  # inv_ids completos que não puderam ser linearizados
    states_explored: int = 0

    @property
    def linearizable(self) -> bool:
        return self.verdict == LINEARIZABLE


def _real_time_preds(ops):
    """i deve vir depois de j se `response(j) < invocation(i)` (j completou antes de i começar)."""
    preds = [set() for _ in ops]
    for i, oi in enumerate(ops):
        for j, oj in enumerate(ops):
            if i == j or oj.response is None:
                continue
            if oj.response < oi.invocation:
                preds[i].add(j)
    return preds


def check_linearizable(ops, *, max_states: int = 500_000, initial_read_seq: int = 0,
                       initial_generation: int = 0, initial_facts: dict | None = None) -> CheckResult:
    """Busca topológica com pruning (WGL). Uma op `i` é candidata a linearizar em seguida quando todos os
    seus predecessores de tempo real ainda pendentes já foram linearizados. Ops incompletas (sem
    `response`) podem ser omitidas (só as COMPLETAS precisam ser linearizadas). Memoização em
    `(mask, state.key())`. Devolve `CheckResult` com contraexemplo mínimo se falhar."""
    ops = list(ops)
    n = len(ops)
    preds = _real_time_preds(ops)
    capture_ids = {o.inv_id for o in ops if o.kind == CAPTURE}
    complete_mask = 0
    for i, o in enumerate(ops):
        if o.response is not None:
            complete_mask |= (1 << i)

    memo = set()
    explored = [0]
    budget_hit = [False]
    best = [-1, 0]     # [max_completed_linearized, mask nessa profundidade] — p/ o contraexemplo mínimo

    def dfs(mask, state):
        if (mask & complete_mask) == complete_mask:
            return True
        if explored[0] >= max_states:
            budget_hit[0] = True                                # orçamento esgotado, NÃO é prova de falha
            return False
        sk = (mask, state.key())
        if sk in memo:
            return False
        explored[0] += 1
        completed = bin(mask & complete_mask).count("1")
        if completed > best[0]:
            best[0], best[1] = completed, mask
        for i in range(n):
            if mask & (1 << i):
                continue
            if any(not (mask & (1 << j)) for j in preds[i]):    # predecessor de tempo real pendente
                continue
            ns = state.copy()
            outcome, expected = apply_op(ns, ops[i], capture_ids)
            if outcome != VALID:                                # INVALID (impossível) / NOT_READY (depois)
                continue
            if ops[i].response is not None and ops[i].result is not None and expected != tuple(ops[i].result):
                continue                                        # esta ordem contradiz a resposta observada
            if dfs(mask | (1 << i), ns):
                return True
        memo.add(sk)
        return False

    init = _State(dict(initial_facts) if initial_facts else {}, {}, initial_read_seq, initial_generation, {})
    if dfs(0, init):
        return CheckResult(LINEARIZABLE, "linearizável", [], explored[0])
    if budget_hit[0]:                                           # §3: orçamento ≠ não-linearizável
        return CheckResult(BUDGET_EXHAUSTED, f"orçamento esgotado ({max_states} estados)", [], explored[0])
    linearized = best[1]
    stuck = [ops[i].inv_id for i in range(n)
             if (complete_mask & (1 << i)) and not (linearized & (1 << i))]
    return CheckResult(NON_LINEARIZABLE, "não-linearizável (nenhuma ordem reproduz as respostas)",
                       stuck, explored[0])


def history_to_json(ops, *, seed=None, schedule=None) -> str:
    """Serialização reproduzível de uma história (para contraexemplos e regressão)."""
    return json.dumps({
        "seed": seed,
        "schedule": schedule,
        "ops": [{"inv_id": o.inv_id, "actor": o.actor, "kind": o.kind, "invocation": o.invocation,
                 "response": o.response, "operation_id": o.operation_id, "fact_id": o.fact_id,
                 "version": o.version, "value": o.value, "result": list(o.result) if o.result else None,
                 "snapshot_of": o.snapshot_of} for o in ops],
    }, indent=2)


def json_to_history(blob: str):
    d = json.loads(blob)
    ops = [Op(o["inv_id"], o["actor"], o["kind"], o["invocation"], o["response"], o["operation_id"],
              o["fact_id"], o["version"], o["value"], tuple(o["result"]) if o["result"] else None,
              o["snapshot_of"]) for o in d["ops"]]
    return ops, d.get("seed"), d.get("schedule")

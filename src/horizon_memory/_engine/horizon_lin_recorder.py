# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gravador de histórias de concorrência (V23-D1 / FH-02) — a ponte entre o runtime real e o checker
puro de linearizabilidade (`horizon_linearizability`).

Contratos (FH-02 §D1):
  - DESLIGADO por padrão; só grava quando `enabled=True`;
  - memória LIMITADA (`capacity`): overflow INVALIDA a amostra explicitamente (nunca produz um PASS
    parcial de uma história truncada);
  - registra invocação, resposta, ator, tipo, DIGEST da entrada, resultado, geração e `read_seq`;
  - NÃO registra payload privado (só um digest da entrada);
  - registra também o ponto interno candidato à linearização (`lin_seq`), mas o checker NÃO o usa para
    "provar" a história — ele depende só de invocação/resposta e efeitos observados.

Cada operação é medida com um relógio lógico monotônico GLOBAL (um contador sob lock), de modo que
`invocation`/`response` de operações concorrentes sejam ordenáveis de forma consistente e a relação de
tempo real `response(A) < invocation(B)` seja bem definida entre threads.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass

from horizon_memory._engine.horizon_linearizability import PRESENT, READ, Op

_VALUE_DOMAIN = b"HORIZON-LIN-VALUE-v1"     # separação de domínio do token de valor


def value_token(value):
    """FH-02.1 §7 — TOKEN determinístico e NÃO-invertível de um valor (digest com separação de domínio).
    O checker só compara IGUALDADE de tokens; o payload em claro NUNCA é armazenado nem serializado. Dois
    valores iguais → mesmo token (dedup/leitura seguem consistentes); `None` → `None`."""
    if value is None:
        return None
    return int.from_bytes(hashlib.sha256(_VALUE_DOMAIN + repr(value).encode()).digest()[:8], "big")


def input_digest(kind, fact_id, version, value, operation_id) -> int:
    """Digest curto e determinístico da ENTRADA (só sobre o TOKEN do valor, nunca o payload em claro)."""
    h = hashlib.sha256(repr((kind, fact_id, version, value_token(value), operation_id)).encode()).digest()
    return int.from_bytes(h[:6], "big")


@dataclass
class _Rec:
    inv_id: int
    actor: str
    kind: str
    invocation: int
    operation_id: int | None
    fact_id: int | None
    version: int | None
    value: int | None
    in_digest: int
    response: int | None = None
    result: object = None
    generation: int | None = None
    read_seq: int | None = None
    lin_seq: int | None = None       # ponto interno candidato (NÃO usado pelo checker)
    snapshot_of: int | None = None


class HistoryRecorder:
    """Gravador thread-safe e bounded. `begin(...)` registra a invocação e devolve um `inv_id`; `end(...)`
    registra a resposta/efeito. `to_history()` converte para `Op`s do checker. `invalid` fica True em
    overflow (a amostra inteira deve ser DESCARTADA, nunca checada parcialmente)."""

    def __init__(self, *, enabled: bool = False, capacity: int = 20_000, synthetic_unsafe: bool = False):
        self.enabled = enabled
        self.capacity = capacity
        # FH-02.1 §7: por PADRÃO os valores viram TOKENS (payload nunca armazenado). `synthetic_unsafe`
        # mantém valores em claro SÓ para testes sintéticos — jamais o default de produção.
        self.synthetic_unsafe = synthetic_unsafe
        self._lock = threading.Lock()
        self._clock = 0
        self._next_inv = 0
        self._recs: dict[int, _Rec] = {}
        self.invalid = False
        self.overflow_reason = ""

    def _tok(self, value):
        return value if self.synthetic_unsafe else value_token(value)

    def _tick(self) -> int:
        self._clock += 1
        return self._clock

    def begin(self, actor, kind, *, fact_id=None, version=None, value=None, operation_id=None,
              snapshot_of=None) -> int | None:
        if not self.enabled:
            return None
        with self._lock:
            if len(self._recs) >= self.capacity:
                self.invalid = True
                self.overflow_reason = "capacidade do gravador excedida"
                return None
            inv_id = self._next_inv
            self._next_inv += 1
            self._recs[inv_id] = _Rec(inv_id, actor, kind, self._tick(), operation_id, fact_id, version,
                                      self._tok(value),                    # valor → TOKEN (§7)
                                      input_digest(kind, fact_id, version, value, operation_id),
                                      snapshot_of=snapshot_of)
            return inv_id

    def end(self, inv_id, result, *, generation=None, read_seq=None, lin_seq=None) -> None:
        if not self.enabled or inv_id is None:
            return
        with self._lock:
            r = self._recs.get(inv_id)
            if r is None:
                return
            # §7: numa leitura, o VALOR do resultado também vira TOKEN (o `seq` de write não é valor)
            if r.kind == READ and isinstance(result, tuple) and len(result) == 2 and result[0] == PRESENT:
                result = (result[0], self._tok(result[1]))
            r.response = self._tick()
            r.result = result
            r.generation = generation
            r.read_seq = read_seq
            r.lin_seq = lin_seq

    def to_history(self):
        """Converte para uma lista de `Op` do checker (só invocação/resposta/efeito; `lin_seq` é ignorado
        pelo checker — carregado só para auditoria). Overflow → `ValueError` (amostra inválida)."""
        if self.invalid:
            raise ValueError(f"amostra inválida: {self.overflow_reason}")
        with self._lock:
            recs = sorted(self._recs.values(), key=lambda r: r.invocation)
        return [Op(r.inv_id, r.actor, r.kind, r.invocation, r.response, r.operation_id, r.fact_id,
                   r.version, r.value, r.result, r.snapshot_of) for r in recs]

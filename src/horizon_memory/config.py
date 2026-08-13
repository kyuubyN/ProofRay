# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Closed, immutable configuration for standalone Horizon Memory instances."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Domínio de valor do substrato validado (bulk/residual armazenam u8). Documentado explicitamente para
# que o contrato público não prometa mais do que o núcleo entrega.
VALUE_MIN = 0
VALUE_MAX = 255

DEFAULT_GENERATION_ID = 1
DEFAULT_FACT_CAPACITY = 2000
DEFAULT_RESIDUAL_DIM = 128
DEFAULT_KEY_ID = 0


@dataclass(frozen=True)
class HorizonConfig:
    """Configuração de uma instância standalone da Horizon Memory ligada a UM scope.

    - `root`: diretório raiz onde o object/WAL store e o publication store são materializados.
    - `scope_id`: identidade do scope. Toda operação pública exige este scope; um scope diferente é
      recusado fail-closed (isolamento de scope é gate não-compensável).
    - `key`: chave HMAC de 32 bytes usada pelo keyring (key_id -> key). Nunca é logada.
    - `generation_id`/`fact_capacity`/`residual_dim`: parâmetros da geração base (genesis).
    """

    root: str
    scope_id: int
    key: bytes = field(repr=False)
    generation_id: int = DEFAULT_GENERATION_ID
    fact_capacity: int = DEFAULT_FACT_CAPACITY
    residual_dim: int = DEFAULT_RESIDUAL_DIM
    key_id: int = DEFAULT_KEY_ID
    # Subdiretórios derivados (relativos a root); expostos para o ledger/auditoria.
    wal_dirname: str = "wal"
    pub_dirname: str = "pub"

    def __post_init__(self) -> None:
        if not isinstance(self.key, (bytes, bytearray)) or len(self.key) != 32:
            raise ValueError("HorizonConfig.key deve ter exatamente 32 bytes")
        # Congela bytearray mutavel e impede alteracao da chave depois da validacao.
        object.__setattr__(self, "key", bytes(self.key))
        if self.scope_id < 0:
            raise ValueError("scope_id deve ser >= 0")
        if self.fact_capacity <= 0:
            raise ValueError("fact_capacity deve ser > 0")

    @property
    def wal_root(self) -> Path:
        return Path(self.root) / self.wal_dirname

    @property
    def pub_root(self) -> Path:
        return Path(self.root) / self.pub_dirname

    def redacted(self) -> dict:
        """Representação segura para logs/resultados: NUNCA inclui a chave."""
        return {
            "root": str(self.root),
            "scope_id": self.scope_id,
            "generation_id": self.generation_id,
            "fact_capacity": self.fact_capacity,
            "residual_dim": self.residual_dim,
            "key_id": self.key_id,
            "key": "<redacted>",
        }

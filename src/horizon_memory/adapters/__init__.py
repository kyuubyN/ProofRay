# SPDX-License-Identifier: Apache-2.0 OR AGPL-3.0-or-later
# Copyright (c) 2026 kyuubyN
"""External reader adapters.

A interface externa (`ModelAdapter.generate`, `ModelAdapter.measure_prefill`) e os adapters concretos
(Qwen2.5-1.5B, Granite local, Gemini remoto) vivem aqui. Nenhum adapter modifica o runtime do VTE nem
coloca lógica do modelo dentro da Horizon Memory.
"""

from .base import (
    FixtureModelAdapter, GenerationConfig, LocalCallableAdapter, ModelAdapter, ModelRun,
    ModelRunState, PrefillMetrics,
)
from .gemini import (
    API_VERSION, DEFAULT_MODEL_ID, SDK_VERSION, GeminiModelAdapter, GeminiPricing,
    RateLimiter, RequestLedger, TransportResponse, scan_for_gemini_secrets,
)

__all__ = [
    "ModelAdapter", "ModelRun", "ModelRunState", "GenerationConfig", "PrefillMetrics",
    "FixtureModelAdapter", "LocalCallableAdapter", "GeminiModelAdapter", "GeminiPricing",
    "RateLimiter", "RequestLedger", "TransportResponse", "scan_for_gemini_secrets",
    "DEFAULT_MODEL_ID", "API_VERSION", "SDK_VERSION",
]

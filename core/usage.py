"""Medición de uso: llamadas, tokens, costo y latencia de un bloque de código."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.llm_client import LLMResponse


@dataclass(eq=False)  # Se identifica por instancia: dos medidores con los mismos totales
class UsageMeter:  # no son el mismo (track() los quita de la lista por identidad).
    """Acumula el uso de las respuestas exitosas. Los intentos fallidos no cuentan."""

    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0

    @property
    def tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def add(self, response: LLMResponse) -> None:
        self.calls += 1
        self.tokens_in += response.tokens_in
        self.tokens_out += response.tokens_out
        self.cost_usd += response.cost_usd
        self.latency_ms += response.latency_ms

"""Traza de un agente: cada paso con lo que observó y lo que costó."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents.budget import StopReason

if TYPE_CHECKING:
    from agents.react_agent import AgentStep

__all__ = ["AgentResult", "StopReason", "TraceStep"]


@dataclass(frozen=True)
class TraceStep:
    paso: AgentStep
    observacion: dict | None
    tokens: int
    cost_usd: float
    latency_ms: float


@dataclass(frozen=True)
class AgentResult:
    respuesta: str | None
    traza: list[TraceStep]
    motivo: StopReason
    llamadas: int
    tokens: int
    cost_usd: float
    latency_ms: float

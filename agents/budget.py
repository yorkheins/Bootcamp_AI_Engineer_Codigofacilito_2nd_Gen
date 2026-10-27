"""Presupuesto del agente y motivos de parada."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agents.trace import TraceStep

StopReason = Literal["respuesta", "max_pasos", "presupuesto", "sin_progreso", "timeout", "error"]


def _firma(step: TraceStep) -> tuple[str, str] | None:
    """Herramienta y argumentos normalizados. None si el paso no es una llamada."""
    paso = step.paso
    if paso.accion != "usar_herramienta":
        return None
    raw = paso.argumentos_json or "{}"
    try:
        argumentos = json.dumps(json.loads(raw), sort_keys=True, separators=(",", ":"))
    except json.JSONDecodeError:
        argumentos = raw
    return (paso.herramienta or "", argumentos)


def _sin_progreso(traza: list[TraceStep]) -> bool:
    """La misma herramienta con los mismos argumentos, dos pasos seguidos."""
    if len(traza) < 2:
        return False
    firma = _firma(traza[-1])
    return firma is not None and firma == _firma(traza[-2])


@dataclass(frozen=True)
class AgentBudget:
    """Límites de un recorrido. Se consultan **antes** de cada paso, nunca después."""

    max_pasos: int = 6
    max_tokens: int = 12_000
    max_usd: float = 0.02
    max_segundos: float = 45

    def exhausted(self, traza: list[TraceStep], elapsed_s: float) -> StopReason | None:
        if len(traza) >= self.max_pasos:
            return "max_pasos"
        if sum(s.tokens for s in traza) >= self.max_tokens:
            return "presupuesto"
        if sum(s.cost_usd for s in traza) >= self.max_usd:
            return "presupuesto"
        if elapsed_s >= self.max_segundos:
            return "timeout"
        if _sin_progreso(traza):
            return "sin_progreso"
        return None

    def exhausted_totals(
        self, pasos: int, tokens: int, cost_usd: float, elapsed_s: float
    ) -> StopReason | None:
        """Las mismas reglas con totales ya sumados.

        PlanAndExecute mide todo el recorrido (planner, executors, replan y síntesis) con
        `UsageMeter`, porque varias de esas llamadas no son pasos de la traza.
        """
        if pasos >= self.max_pasos:
            return "max_pasos"
        if tokens >= self.max_tokens or cost_usd >= self.max_usd:
            return "presupuesto"
        if elapsed_s >= self.max_segundos:
            return "timeout"
        return None

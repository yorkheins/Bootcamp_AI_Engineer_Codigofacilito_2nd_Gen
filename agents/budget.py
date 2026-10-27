"""Presupuesto del agente y motivos de parada."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agents.trace import TraceStep

StopReason = Literal["respuesta", "max_pasos", "presupuesto", "sin_progreso", "timeout", "error"]


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
        # TODO(clase-4): práctica guiada. Si la misma herramienta se llamó con los mismos
        # argumentos dos pasos seguidos, regresa "sin_progreso".
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

"""Plan-and-execute: primero un plan completo, luego un executor ReAct por paso."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from agents.budget import AgentBudget, StopReason
from agents.react_agent import PROMPTS_DIR, ReActAgent, describe_tools
from agents.trace import AgentResult, TraceStep
from contracts.structured_output import StructuredOutputError, generate_structured
from core.errors import AllProvidersFailedError
from prompting import PromptKit

if TYPE_CHECKING:
    from agents.react_agent import EventHandler
    from agents.tools import ToolRunner
    from contracts.tool_schemas import ToolSpec
    from core.llm_client import LLMClient
    from core.usage import UsageMeter

COMPANY = "Acme Operaciones"
EXECUTOR_BUDGET = AgentBudget(max_pasos=2)  # Un paso del plan: una herramienta y responder.


class Paso(BaseModel):
    objetivo: str
    herramienta: str | None


class Plan(BaseModel):
    razonamiento: str
    # max_length=5 es intencional: el plan es un contrato; un plan más largo se rechaza.
    pasos: list[Paso] = Field(min_length=1, max_length=5)


class _PlanStopped(Exception):
    def __init__(self, motivo: StopReason) -> None:
        self.motivo = motivo


class PlanAndExecute:
    def __init__(
        self,
        client: LLMClient,
        tools: list[ToolSpec],
        *,
        budget: AgentBudget | None = None,
        max_replans: int = 1,
        clock: Callable[[], float] = time.monotonic,
        on_event: EventHandler | None = None,
        runner: ToolRunner | None = None,
    ) -> None:
        self.client = client
        self.tools = list(tools)
        self.budget = budget or AgentBudget()
        self.max_replans = max_replans
        self.runner = runner  # Lo comparten todos los executors: un solo tope por herramienta.
        self.clock = clock
        self.on_event = on_event
        kit = PromptKit(PROMPTS_DIR)
        self.planner_prompt = kit.get("planner")
        self.executor_prompt = kit.get("react_agent")

    def _emit(self, kind: str, **data: Any) -> None:
        if self.on_event:
            self.on_event(kind, data)

    def _forward(self, kind: str, data: dict[str, Any]) -> None:
        """Pasa a la CLI los pasos y observaciones de los executors (no su "fin")."""
        if kind != "fin":
            self._emit(kind, **data)

    def run(self, pregunta: str) -> AgentResult:
        run_id = uuid.uuid4().hex[:8]
        start = self.clock()
        traza: list[TraceStep] = []
        resultados: list[str] = []

        with self.client.track() as total:

            def check() -> None:
                motivo = self.budget.exhausted_totals(
                    len(traza), total.tokens, total.cost_usd, self.clock() - start
                )
                if motivo:
                    raise _PlanStopped(motivo)

            try:
                plan = self._plan(pregunta, resultados, run_id)
                self._emit("plan", plan=plan, replan=False)
                for replans in range(self.max_replans + 1):
                    failed = False
                    for paso in plan.pasos:
                        check()
                        result = self._execute(paso, resultados, run_id)
                        traza.extend(result.traza)
                        errores = [
                            str(step.observacion["error"])
                            for step in result.traza
                            if "error" in (step.observacion or {})
                        ]
                        failed = result.respuesta is None or bool(errores)
                        respuesta = result.respuesta or f"sin respuesta ({result.motivo})"
                        if errores:
                            respuesta += f" (error de herramienta: {'; '.join(errores)})"
                        resultados.append(f"{paso.objetivo} -> {respuesta}")
                        if failed:
                            break
                    if not failed or replans == self.max_replans:
                        break
                    check()
                    plan = self._plan(pregunta, resultados, run_id)
                    self._emit("plan", plan=plan, replan=True)

                check()
                final = self._synthesize(pregunta, resultados, run_id)
            except _PlanStopped as stop:
                return self._finish(None, traza, stop.motivo, total)
            except (StructuredOutputError, AllProvidersFailedError):
                return self._finish(None, traza, "error", total)
            return self._finish(final.respuesta, traza, final.motivo, total)

    def _plan(self, pregunta: str, resultados: list[str], run_id: str) -> Plan:
        messages = self.planner_prompt.render(
            company=COMPANY,
            tools=describe_tools(self.tools),
            pregunta=pregunta,
            aprendido="\n".join(resultados),
        )
        log_extra = {
            "prompt_name": self.planner_prompt.name,
            "prompt_version": self.planner_prompt.version,
            "agent_kind": "plan",
            "agent_run_id": run_id,
        }
        return generate_structured(
            self.client, messages, Plan, log_extra=log_extra, **self.planner_prompt.model_params
        )

    def _executor(self, tools: list[ToolSpec], run_id: str) -> ReActAgent:
        return ReActAgent(
            self.client,
            tools,
            budget=EXECUTOR_BUDGET,
            prompt=self.executor_prompt,
            clock=self.clock,
            on_event=self._forward,
            agent_kind="plan",
            run_id=run_id,
            runner=self.runner,
        )

    def _execute(self, paso: Paso, resultados: list[str], run_id: str) -> AgentResult:
        contexto = [f"Objetivo de este paso: {paso.objetivo}"]
        if paso.herramienta:
            contexto.append(f"Herramienta sugerida: {paso.herramienta}")
        if resultados:
            contexto.append("Resultados de pasos anteriores:\n" + "\n".join(resultados))
        return self._executor(self.tools, run_id).run("\n".join(contexto))

    def _synthesize(self, pregunta: str, resultados: list[str], run_id: str) -> AgentResult:
        """Una llamada final, sin herramientas, que responde con todos los resultados."""
        contexto = (
            f"{pregunta}\n\nResultados obtenidos al ejecutar el plan:\n"
            + "\n".join(resultados)
            + "\n\nResponde la pregunta usando solo estos resultados."
        )
        return self._executor([], run_id).run(contexto)

    def _finish(
        self,
        respuesta: str | None,
        traza: list[TraceStep],
        motivo: StopReason,
        usage: UsageMeter,
    ) -> AgentResult:
        result = AgentResult(
            respuesta=respuesta,
            traza=traza,
            motivo=motivo,
            llamadas=usage.calls,
            tokens=usage.tokens,
            cost_usd=usage.cost_usd,
            latency_ms=usage.latency_ms,
        )
        if usage.calls and self.client.last_request_id:
            self.client.logger.annotate(self.client.last_request_id, stop_reason=motivo)
        self._emit("fin", resultado=result)
        return result

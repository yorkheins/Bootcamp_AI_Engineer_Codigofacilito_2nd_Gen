from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents import AgentBudget, Plan, PlanAndExecute
from contracts import BUSCAR_TICKET
from core.llm_client import LLMClient
from tests.agent_helpers import answer, plan, reply, use_tool
from tests.conftest import FakeProvider, read_events

QUESTION = "Compara el T-1042 y el T-1014."
TWO_STEPS = plan(("Estado del T-1042", "buscar_ticket"), ("Estado del T-1014", "buscar_ticket"))


def make_planner(script, logger, fake_sleep, **kwargs):
    provider = FakeProvider("gemini", [reply(p) for p in script])
    client = LLMClient([provider], logger, sleep=fake_sleep)
    events: list[tuple[str, dict]] = []
    agent = PlanAndExecute(
        client, [BUSCAR_TICKET], on_event=lambda k, d: events.append((k, d)), **kwargs
    )
    return provider, agent, events


def test_valid_plan_runs_step_by_step(logger, log_path, fake_sleep):
    script = [
        TWO_STEPS,
        use_tool(ticket_id="T-1042"),
        answer("T-1042 en revisión."),
        use_tool(ticket_id="T-1014"),
        answer("T-1014 esperando proveedor."),
        answer("El T-1042 va más avanzado."),
    ]
    provider, agent, _ = make_planner(script, logger, fake_sleep)

    result = agent.run(QUESTION)

    assert result.motivo == "respuesta"
    assert result.respuesta == "El T-1042 va más avanzado."
    assert [s.observacion["id"] for s in result.traza] == ["T-1042", "T-1014"]
    assert result.llamadas == 6
    assert "T-1042 en revisión." in provider.calls[3]["messages"][-1]["content"]
    events = read_events(log_path)
    assert {e["agent_kind"] for e in events} == {"plan"}
    assert len({e["agent_run_id"] for e in events}) == 1
    assert events[-1]["stop_reason"] == "respuesta"


def test_plan_with_six_steps_is_invalid():
    with pytest.raises(ValidationError):
        Plan.model_validate(plan(*[(f"paso {i}", None) for i in range(6)]))
    with pytest.raises(ValidationError):
        Plan.model_validate(plan())


def test_replans_after_step_with_error(logger, fake_sleep):
    script = [
        plan(("Buscar ticket", "buscar_ticket")),
        use_tool(ticket_id="T-9999"),
        answer("No existe."),
        plan(("Confirmar que no existe", None), razonamiento="Replan."),
        answer("Confirmado: no existe."),
        answer("El T-9999 no existe."),
    ]
    provider, agent, events = make_planner(script, logger, fake_sleep)

    result = agent.run("¿Cómo va el T-9999?")

    assert result.motivo == "respuesta"
    plans = [d for k, d in events if k == "plan"]
    assert [p["replan"] for p in plans] == [False, True]
    replan_context = provider.calls[3]["messages"][-1]["content"]
    assert "ticket T-9999 no existe" in replan_context  # el replan ve el error de la herramienta


def test_respects_max_replans(logger, fake_sleep):
    failing = plan(("Buscar ticket", "buscar_ticket"))
    script = [
        failing,
        use_tool(ticket_id="T-9999"),
        answer("No existe."),
        failing,
        use_tool(ticket_id="T-9998"),
        answer("Tampoco existe."),
        answer("Ninguno existe."),
    ]
    provider, agent, events = make_planner(script, logger, fake_sleep, max_replans=1)

    result = agent.run("¿Cómo van el T-9999 y el T-9998?")

    assert result.motivo == "respuesta"
    assert sum(1 for k, _ in events if k == "plan") == 2  # plan inicial + un solo replan
    assert len(provider.calls) == 7


def test_global_budget_stops_execution(logger, fake_sleep):
    script = [
        reply(TWO_STEPS, tokens_in=500, tokens_out=100),
        use_tool(ticket_id="T-1042"),
        answer("no debería llegar"),
    ]
    provider = FakeProvider("gemini", [s if not isinstance(s, dict) else reply(s) for s in script])
    client = LLMClient([provider], logger, sleep=fake_sleep)
    agent = PlanAndExecute(client, [BUSCAR_TICKET], budget=AgentBudget(max_tokens=600))

    result = agent.run(QUESTION)

    assert result.motivo == "presupuesto"
    assert len(provider.calls) == 1


def test_plan_is_emitted_before_execution(logger, fake_sleep):
    script = [
        TWO_STEPS,
        use_tool(ticket_id="T-1042"),
        answer("a"),
        use_tool(ticket_id="T-1014"),
        answer("b"),
        answer("c"),
    ]
    _, agent, events = make_planner(script, logger, fake_sleep)

    agent.run(QUESTION)

    kinds = [k for k, _ in events]
    assert kinds[0] == "plan"
    assert kinds.index("plan") < kinds.index("paso")
    assert len(events[0][1]["plan"].pasos) == 2
    assert kinds[-1] == "fin"

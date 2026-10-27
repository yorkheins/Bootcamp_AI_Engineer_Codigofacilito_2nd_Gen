from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agents import AgentBudget, AgentStep, ReActAgent
from agents.react_agent import OBSERVATION_LIMIT, TRUNCATION_MARK
from contracts import BUSCAR_TICKET, ToolSpec
from core.llm_client import LLMClient
from tests.agent_helpers import FakeClock, answer, reply, use_tool
from tests.conftest import FakeProvider, read_events

QUESTION = "¿Cómo va el T-1042?"


def make_agent(script, logger, fake_sleep, *, tools=None, **kwargs):
    provider = FakeProvider("gemini", [reply(p) if isinstance(p, dict) else p for p in script])
    client = LLMClient([provider], logger, sleep=fake_sleep)
    agent = ReActAgent(client, tools or [BUSCAR_TICKET], **kwargs)
    return provider, agent


def user_content(provider, call: int) -> str:
    return provider.calls[call]["messages"][-1]["content"]


def test_answers_without_tools(logger, fake_sleep):
    _, agent = make_agent([answer("Hola, ¿en qué te ayudo?")], logger, fake_sleep)

    result = agent.run("Hola")

    assert result.motivo == "respuesta"
    assert result.respuesta == "Hola, ¿en qué te ayudo?"
    assert result.traza == []
    assert (result.llamadas, result.tokens) == (1, 15)


def test_one_tool_then_answer(logger, fake_sleep):
    events = []
    provider, agent = make_agent(
        [use_tool(), answer("Está en revisión.")],
        logger,
        fake_sleep,
        on_event=lambda kind, data: events.append(kind),
    )

    result = agent.run(QUESTION)

    assert result.motivo == "respuesta"
    [step] = result.traza
    assert step.paso.herramienta == "buscar_ticket"
    assert step.observacion["estado"] == "en revisión"
    assert step.tokens == 15
    context = user_content(provider, 1)
    assert "<traza>" in context
    assert "Paso 1 · Pensamiento:" in context and "Paso 1 · Observación:" in context
    assert events == ["paso", "observacion", "paso", "fin"]


def test_error_observation_then_corrects(logger, fake_sleep):
    provider, agent = make_agent(
        [use_tool(ticket_id="1042"), use_tool(ticket_id="T-1042"), answer("Corregido.")],
        logger,
        fake_sleep,
    )

    result = agent.run(QUESTION)

    assert result.motivo == "respuesta"
    first, second = result.traza
    assert first.observacion["error"].startswith("Argumentos inválidos")
    assert second.observacion["id"] == "T-1042"
    assert "Argumentos inválidos" in user_content(provider, 1)


def test_max_pasos(logger, fake_sleep):
    _, agent = make_agent(
        [use_tool(), use_tool(ticket_id="T-1043")], logger, fake_sleep, budget=AgentBudget(2)
    )

    result = agent.run(QUESTION)

    assert result.motivo == "max_pasos"
    assert result.respuesta is None
    assert len(result.traza) == 2


def test_presupuesto_with_fake_tokens(logger, fake_sleep):
    script = [reply(use_tool(), tokens_in=900, tokens_out=200), answer("no debería llegar")]
    provider, agent = make_agent(script, logger, fake_sleep, budget=AgentBudget(max_tokens=1000))

    result = agent.run(QUESTION)

    assert result.motivo == "presupuesto"
    assert len(provider.calls) == 1  # se detiene antes del segundo paso


def test_timeout_with_fake_clock(logger, fake_sleep):
    _, agent = make_agent(
        [use_tool(), answer()],
        logger,
        fake_sleep,
        budget=AgentBudget(max_segundos=10),
        clock=FakeClock(step=6),
    )

    result = agent.run(QUESTION)

    assert result.motivo == "timeout"
    assert len(result.traza) == 1


def test_structured_output_error_ends_with_error_and_trace(logger, fake_sleep):
    invalid = {"x": 1}  # no es un AgentStep: falla la validación y se agotan las reparaciones
    _, agent = make_agent([use_tool(), invalid, invalid, invalid], logger, fake_sleep)

    result = agent.run(QUESTION)

    assert result.motivo == "error"
    assert len(result.traza) == 1
    assert result.respuesta is None


def test_long_observations_are_truncated_in_context(logger, fake_sleep):
    def big(args) -> dict:
        return {"dato": "x" * (OBSERVATION_LIMIT * 2)}

    tool = ToolSpec("enorme", "Regresa mucho texto.", BUSCAR_TICKET.args_model, big)
    provider, agent = make_agent([use_tool("enorme"), answer()], logger, fake_sleep, tools=[tool])

    agent.run(QUESTION)

    context = user_content(provider, 1)
    assert TRUNCATION_MARK in context
    assert "x" * (OBSERVATION_LIMIT * 2) not in context


def test_invalid_step_triggers_repair(logger, fake_sleep):
    invalid = {**answer(), "respuesta": None}  # responder sin respuesta
    provider, agent = make_agent([invalid, answer("Ahora sí.")], logger, fake_sleep)

    result = agent.run("Hola")

    assert result.respuesta == "Ahora sí."
    assert result.llamadas == 2
    assert 'debes escribir la "respuesta" final' in user_content(provider, 1)


@pytest.mark.parametrize(
    "payload",
    [
        {**use_tool(), "herramienta": None},
        {**use_tool(), "argumentos_json": "{no es json"},
        {**answer(), "respuesta": ""},
    ],
)
def test_agent_step_validator(payload):
    with pytest.raises(ValidationError):
        AgentStep.model_validate(payload)


def test_agent_step_field_order_and_tool_call():
    assert next(iter(AgentStep.model_fields)) == "pensamiento"
    call = AgentStep.model_validate(use_tool()).as_tool_call("paso-1")
    assert (call.id, call.name, json.loads(call.arguments)) == (
        "paso-1",
        "buscar_ticket",
        {"ticket_id": "T-1042"},
    )


def test_log_has_agent_fields_and_stop_reason(logger, log_path, fake_sleep):
    _, agent = make_agent([use_tool(), answer()], logger, fake_sleep)

    agent.run(QUESTION)

    first, last = read_events(log_path)
    assert first["agent_kind"] == last["agent_kind"] == "react"
    assert first["agent_run_id"] == last["agent_run_id"]
    assert (first["agent_step"], last["agent_step"]) == (1, 2)
    assert "stop_reason" not in first
    assert last["stop_reason"] == "respuesta"
    assert first["structured_schema"] == "AgentStep"


def test_stop_reason_logged_when_budget_stops(logger, log_path, fake_sleep):
    _, agent = make_agent([use_tool()], logger, fake_sleep, budget=AgentBudget(max_pasos=1))

    result = agent.run(QUESTION)

    assert result.motivo == "max_pasos"
    [event] = read_events(log_path)
    assert event["stop_reason"] == "max_pasos"

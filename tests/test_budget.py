from __future__ import annotations

from agents import AgentBudget, AgentStep, TraceStep


def step(
    tokens: int = 100,
    cost: float = 0.001,
    *,
    herramienta: str = "buscar_ticket",
    argumentos: str = '{"ticket_id": "T-1042"}',
) -> TraceStep:
    paso = AgentStep(
        pensamiento="Busco el ticket.",
        accion="usar_herramienta",
        herramienta=herramienta,
        argumentos_json=argumentos,
        respuesta=None,
    )
    return TraceStep(paso, {"estado": "abierto"}, tokens, cost, 10.0)


def test_defaults_match_slide():
    budget = AgentBudget()
    assert (budget.max_pasos, budget.max_tokens, budget.max_usd, budget.max_segundos) == (
        6,
        12_000,
        0.02,
        45,
    )


def test_not_exhausted_with_room():
    assert AgentBudget().exhausted([step()], elapsed_s=1) is None


def test_max_pasos():
    assert AgentBudget(max_pasos=2).exhausted([step(), step()], elapsed_s=0) == "max_pasos"


def test_presupuesto_by_tokens():
    budget = AgentBudget(max_tokens=250)
    assert budget.exhausted([step(tokens=200)], 0) is None
    assert budget.exhausted([step(tokens=200), step(tokens=50)], 0) == "presupuesto"


def test_presupuesto_by_usd():
    budget = AgentBudget(max_usd=0.002)
    assert budget.exhausted([step(cost=0.0015)], 0) is None
    assert budget.exhausted([step(cost=0.0015), step(cost=0.0005)], 0) == "presupuesto"


def test_sin_progreso_when_the_same_call_repeats():
    repetido = step()
    assert AgentBudget().exhausted([repetido, repetido], elapsed_s=0) == "sin_progreso"


def test_sin_progreso_ignores_json_spacing():
    compacto = step(argumentos='{"ticket_id":"T-1042"}')
    espaciado = step(argumentos='{"ticket_id": "T-1042"}')
    assert AgentBudget().exhausted([compacto, espaciado], elapsed_s=0) == "sin_progreso"


def test_different_args_or_tool_is_not_sin_progreso():
    budget = AgentBudget()
    assert budget.exhausted([step(), step(argumentos='{"ticket_id": "T-1043"}')], 0) is None
    assert budget.exhausted([step(), step(herramienta="consultar_proveedor")], 0) is None
    assert budget.exhausted([step()], 0) is None


def test_timeout():
    budget = AgentBudget(max_segundos=10)
    assert budget.exhausted([], elapsed_s=9.9) is None
    assert budget.exhausted([], elapsed_s=10) == "timeout"


def test_exhausted_totals_uses_same_rules():
    budget = AgentBudget(max_pasos=3, max_tokens=100, max_usd=0.01, max_segundos=5)
    assert budget.exhausted_totals(1, 50, 0.001, 1) is None
    assert budget.exhausted_totals(3, 0, 0, 0) == "max_pasos"
    assert budget.exhausted_totals(0, 100, 0, 0) == "presupuesto"
    assert budget.exhausted_totals(0, 0, 0.01, 0) == "presupuesto"
    assert budget.exhausted_totals(0, 0, 0, 5) == "timeout"

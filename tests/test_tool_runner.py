from __future__ import annotations

import json
import threading

from pydantic import BaseModel

from agents.tools import POLITICAS, ToolPolicy, ToolRunner, TransientToolError
from contracts.tool_schemas import ACTUALIZAR_ESTADO, BUSCAR_TICKET, ToolSpec
from core.llm_client import ToolCall


class EchoArgs(BaseModel):
    texto: str


def spec(name: str, handler) -> ToolSpec:
    return ToolSpec(
        name=name, description=f"Herramienta {name}", args_model=EchoArgs, handler=handler
    )


def run(runner: ToolRunner, name: str, arguments: str = '{"texto": "hola"}') -> dict:
    message = runner.execute(ToolCall("c1", name, arguments))
    assert message["role"] == "tool" and message["tool_call_id"] == "c1"
    return json.loads(message["content"])


def error_tipo(result: dict) -> str | None:
    return result["error"]["tipo"] if "error" in result else None


def test_read_role_cannot_update_state():
    runner = ToolRunner([BUSCAR_TICKET, ACTUALIZAR_ESTADO], rol="lectura")
    result = run(runner, "actualizar_estado", '{"ticket_id": "T-1042", "estado": "resuelto"}')
    assert error_tipo(result) == "permiso"
    assert runner.eventos[-1] == {
        "tool_name": "actualizar_estado",
        "tool_permiso": "escritura",
        "tool_resultado": "permiso",
        "tool_intentos": 0,
    }


def test_operator_role_can_read_and_write():
    runner = ToolRunner([BUSCAR_TICKET, ACTUALIZAR_ESTADO], rol="operador")
    assert runner.permitida("buscar_ticket") and runner.permitida("actualizar_estado")


def test_tool_without_policy_is_denied():
    calls = []
    runner = ToolRunner([spec("borrar_todo", lambda args: calls.append(args) or {})])
    assert runner.politica("borrar_todo").permiso == "peligrosa"
    assert error_tipo(run(runner, "borrar_todo")) == "permiso"
    assert calls == []


def test_max_llamadas_per_tool():
    politicas = {"eco": ToolPolicy("lectura", max_llamadas=2)}
    runner = ToolRunner([spec("eco", lambda args: {"eco": args.texto})], politicas=politicas)
    assert run(runner, "eco") == {"eco": "hola"}
    assert run(runner, "eco") == {"eco": "hola"}
    assert error_tipo(run(runner, "eco")) == "limite"


def test_invalid_arguments_return_clear_validation_error():
    runner = ToolRunner([BUSCAR_TICKET])
    result = run(runner, "buscar_ticket", '{"ticket_id": "1042"}')
    assert error_tipo(result) == "validacion"
    assert "ticket_id" in result["error"]["mensaje"]


def test_unknown_tool():
    result = run(ToolRunner([BUSCAR_TICKET]), "no_existe")
    assert error_tipo(result) == "validacion"
    assert "buscar_ticket" in result["error"]["sugerencia"]


def test_slow_handler_times_out_and_retries(fake_sleep):
    release = threading.Event()

    def slow(args):
        release.wait(2)
        return {"ok": True}

    politicas = {"lento": ToolPolicy("lectura", timeout_s=0.05)}
    runner = ToolRunner([spec("lento", slow)], politicas=politicas, sleep=fake_sleep)
    try:
        result = run(runner, "lento")
    finally:
        release.set()
    assert error_tipo(result) == "transitorio"
    assert result["error"]["reintentable"] is True
    assert fake_sleep.waits == [0.5, 1.0]
    assert runner.eventos[-1]["tool_intentos"] == 3


def test_transient_idempotent_is_retried_until_success(fake_sleep):
    attempts = []

    def flaky(args):
        attempts.append(1)
        if len(attempts) < 3:
            raise TransientToolError("servicio saturado")
        return {"ok": True}

    politicas = {"flaky": ToolPolicy("lectura")}
    runner = ToolRunner([spec("flaky", flaky)], politicas=politicas, sleep=fake_sleep)
    assert run(runner, "flaky") == {"ok": True}
    assert fake_sleep.waits == [0.5, 1.0]
    assert runner.eventos[-1] == {
        "tool_name": "flaky",
        "tool_permiso": "lectura",
        "tool_resultado": "ok",
        "tool_intentos": 3,
    }


def test_transient_not_idempotent_is_not_retried(fake_sleep):
    attempts = []

    def write_once(args):
        attempts.append(1)
        raise TransientToolError("se cortó la conexión")

    politicas = {"escribe": ToolPolicy("escritura", idempotente=False)}
    runner = ToolRunner([spec("escribe", write_once)], politicas=politicas, sleep=fake_sleep)
    result = run(runner, "escribe")
    assert error_tipo(result) == "transitorio"
    assert result["error"]["reintentable"] is False
    assert len(attempts) == 1
    assert fake_sleep.waits == []


def test_legacy_error_is_normalized():
    runner = ToolRunner([BUSCAR_TICKET])
    result = run(runner, "buscar_ticket", '{"ticket_id": "T-9999"}')
    assert result == {
        "error": {
            "tipo": "negocio",
            "mensaje": "ticket T-9999 no existe",
            "reintentable": False,
            "sugerencia": None,
        }
    }


def test_exception_becomes_permanent_without_traceback(caplog):
    def boom(args):
        raise RuntimeError("detalle interno: /etc/secreto")

    runner = ToolRunner([spec("falla", boom)], politicas={"falla": ToolPolicy("lectura")})
    result = run(runner, "falla")
    assert result["error"]["tipo"] == "permanente"
    assert result["error"]["mensaje"] == "La herramienta falla falló"
    assert "secreto" not in json.dumps(result) and "Traceback" not in json.dumps(result)
    assert "La herramienta falla falló" in caplog.text


def test_call_returns_content_not_tool_message():
    result = ToolRunner([BUSCAR_TICKET]).call("buscar_ticket", {"ticket_id": "T-1042"})
    assert result["id"] == "T-1042"


def test_log_file_has_names_never_arguments(tmp_path):
    log = tmp_path / "tool_calls.jsonl"
    runner = ToolRunner([BUSCAR_TICKET], log_path=log)
    runner.call("buscar_ticket", {"ticket_id": "T-1042"})
    [line] = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert line["tool_name"] == "buscar_ticket" and line["tool_resultado"] == "ok"
    assert "T-1042" not in log.read_text(encoding="utf-8")


def test_default_policies():
    assert POLITICAS["buscar_ticket"] == ToolPolicy("lectura", timeout_s=2)
    assert POLITICAS["actualizar_estado"] == ToolPolicy("escritura")


def test_react_agent_uses_runner_policy(logger, fake_sleep):
    from agents import ReActAgent
    from core.llm_client import LLMClient
    from tests.agent_helpers import answer, reply, use_tool
    from tests.conftest import FakeProvider

    script = [
        use_tool("actualizar_estado", ticket_id="T-1042", estado="resuelto"),
        answer("No tengo permiso para cambiar el estado."),
    ]
    provider = FakeProvider("gemini", [reply(p) for p in script])
    client = LLMClient([provider], logger, sleep=fake_sleep)
    tools = [BUSCAR_TICKET, ACTUALIZAR_ESTADO]
    runner = ToolRunner(tools, rol="lectura")

    result = ReActAgent(client, tools, runner=runner).run("Marca el T-1042 como resuelto")

    assert result.motivo == "respuesta"
    assert result.traza[0].observacion["error"]["tipo"] == "permiso"
    assert runner.eventos == [
        {
            "tool_name": "actualizar_estado",
            "tool_permiso": "escritura",
            "tool_resultado": "permiso",
            "tool_intentos": 0,
        }
    ]


def test_plan_and_execute_shares_runner_with_executors(logger, fake_sleep):
    from agents import PlanAndExecute
    from core.llm_client import LLMClient
    from tests.agent_helpers import answer, plan, reply, use_tool
    from tests.conftest import FakeProvider

    script = [
        plan(("Buscar el ticket", "buscar_ticket")),
        use_tool("buscar_ticket", ticket_id="T-1042"),
        answer("Está en revisión."),
        answer("El T-1042 está en revisión."),
    ]
    provider = FakeProvider("gemini", [reply(p) for p in script])
    client = LLMClient([provider], logger, sleep=fake_sleep)
    runner = ToolRunner([BUSCAR_TICKET], rol="lectura")

    result = PlanAndExecute(client, [BUSCAR_TICKET], runner=runner).run("¿Cómo va el T-1042?")

    assert result.motivo == "respuesta"
    assert [e["tool_name"] for e in runner.eventos] == ["buscar_ticket"]

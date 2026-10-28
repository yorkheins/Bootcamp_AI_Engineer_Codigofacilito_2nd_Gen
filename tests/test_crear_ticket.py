"""Reto de la Clase 6: crear_ticket es idempotente y la llave no la elige el modelo."""

from __future__ import annotations

import json

from agents.tools import POLITICAS, ToolPolicy, ToolRunner
from contracts.tool_schemas import CREAR_TICKET, CrearTicketArgs
from tools import tickets

ARGS = {
    "asunto": "Falla en pagos",
    "descripcion": "No timbra desde las 9.",
    "prioridad": "alta",
}


def test_schema_does_not_ask_the_model_for_a_key():
    assert set(CrearTicketArgs.model_fields) == {"asunto", "descripcion", "prioridad"}
    parameters = CREAR_TICKET.to_openai()["function"]["parameters"]["properties"]
    assert "idempotency_key" not in parameters
    assert "request_id" not in parameters
    assert POLITICAS["crear_ticket"] == ToolPolicy("escritura", idempotente=True)


def test_two_equal_calls_with_the_same_request_create_one_ticket():
    before = tickets.TICKETS_DB.read_bytes()
    runner = ToolRunner([CREAR_TICKET], rol="operador", request_id="req-1")
    first = runner.call("crear_ticket", ARGS)
    second = runner.call(
        "crear_ticket",
        {**ARGS, "asunto": "  FALLA   en pagos!", "descripcion": "no timbra, desde las 9"},
    )

    assert first == {
        "id": "T-2001",
        "asunto": "Falla en pagos",
        "descripcion": "No timbra desde las 9.",
        "prioridad": "alta",
        "creado": True,
    }
    assert second["id"] == "T-2001"
    assert second["creado"] is False
    assert "idempotency_key" not in first
    stored = json.loads(tickets.NUEVOS_PATH.read_text(encoding="utf-8"))
    assert [ticket["id"] for ticket in stored["tickets"]] == ["T-2001"]
    assert tickets.TICKETS_DB.read_bytes() == before


def test_other_request_id_creates_another_ticket():
    first = ToolRunner([CREAR_TICKET], rol="operador", request_id="req-1").call(
        "crear_ticket", ARGS
    )
    second = ToolRunner([CREAR_TICKET], rol="operador", request_id="req-2").call(
        "crear_ticket", ARGS
    )
    assert first["id"] == "T-2001" and first["creado"] is True
    assert second["id"] == "T-2002" and second["creado"] is True


def test_read_role_cannot_create_a_ticket():
    runner = ToolRunner([CREAR_TICKET], rol="lectura", request_id="req-1")
    result = runner.call("crear_ticket", ARGS)
    assert result["error"]["tipo"] == "permiso"
    assert not tickets.NUEVOS_PATH.exists()

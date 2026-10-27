from __future__ import annotations

import hashlib

from contracts.tool_schemas import ActualizarEstadoArgs, BuscarTicketArgs
from tools.tickets import TICKETS_DB, actualizar_estado, buscar_ticket


def digest() -> str:
    return hashlib.sha256(TICKETS_DB.read_bytes()).hexdigest()


def test_update_is_reflected_by_buscar_ticket(_isolated_ticket_states):
    before = buscar_ticket(BuscarTicketArgs(ticket_id="T-1042"))["estado"]
    assert before != "resuelto"

    result = actualizar_estado(ActualizarEstadoArgs(ticket_id="T-1042", estado="resuelto"))

    assert result == {"id": "T-1042", "estado": "resuelto", "cambio": True}
    assert buscar_ticket(BuscarTicketArgs(ticket_id="T-1042"))["estado"] == "resuelto"
    assert _isolated_ticket_states.exists()


def test_same_state_twice_is_idempotent():
    args = ActualizarEstadoArgs(ticket_id="T-1042", estado="esperando proveedor")
    assert actualizar_estado(args)["cambio"] is True
    assert actualizar_estado(args) == {
        "id": "T-1042",
        "estado": "esperando proveedor",
        "cambio": False,
    }


def test_setting_current_state_reports_no_change():
    current = buscar_ticket(BuscarTicketArgs(ticket_id="T-1042"))["estado"]
    result = actualizar_estado(ActualizarEstadoArgs(ticket_id="T-1042", estado=current))
    assert result["cambio"] is False


def test_unknown_ticket_is_business_error(_isolated_ticket_states):
    result = actualizar_estado(ActualizarEstadoArgs(ticket_id="T-9999", estado="resuelto"))
    assert result["error"]["tipo"] == "negocio"
    assert "T-9999" in result["error"]["mensaje"]
    assert not _isolated_ticket_states.exists()


def test_tickets_db_never_changes():
    before = digest()
    actualizar_estado(ActualizarEstadoArgs(ticket_id="T-1042", estado="resuelto"))
    actualizar_estado(ActualizarEstadoArgs(ticket_id="T-1014", estado="abierto"))
    assert digest() == before


def test_overlay_write_is_atomic(_isolated_ticket_states):
    actualizar_estado(ActualizarEstadoArgs(ticket_id="T-1042", estado="resuelto"))
    leftovers = list(_isolated_ticket_states.parent.glob("*.tmp"))
    assert leftovers == []

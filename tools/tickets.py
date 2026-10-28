"""Herramientas de tickets sobre data/tickets_db.json.

`data/tickets_db.json` es de solo lectura. Los cambios de estado van a un overlay,
`var/tickets_estado.json` (`{ticket_id: estado}`), que `buscar_ticket` aplica al leer.
Los tickets nuevos van a `var/tickets_nuevos.json`, con IDs `T-2xxx`.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.tool_schemas import (
        ActualizarEstadoArgs,
        BuscarTicketArgs,
        CrearTicketArgs,
        ListarTicketsPorProveedorArgs,
    )

# Rutas fijas: nunca se construyen a partir de los argumentos del modelo.
TICKETS_DB = Path(__file__).resolve().parents[1] / "data" / "tickets_db.json"
ESTADOS_PATH = Path(__file__).resolve().parents[1] / "var" / "tickets_estado.json"
NUEVOS_PATH = Path(__file__).resolve().parents[1] / "var" / "tickets_nuevos.json"
HISTORY_LIMIT = 3
LISTA_LIMITE = 10
NUEVO_ID_INICIO = 2001


@lru_cache(maxsize=1)
def _tickets() -> dict[str, dict]:
    tickets = json.loads(TICKETS_DB.read_text(encoding="utf-8"))
    return {ticket["id"]: ticket for ticket in tickets}


def buscar_ticket(args: BuscarTicketArgs) -> dict:
    """Estado, equipo, proveedor y últimas entradas del historial de un ticket."""
    ticket = _tickets().get(args.ticket_id)
    if ticket is None:
        return {"error": f"ticket {args.ticket_id} no existe"}
    return {
        "id": ticket["id"],
        "asunto": ticket["asunto"],
        "estado": _estados().get(ticket["id"], ticket["estado"]),
        "equipo": ticket["equipo"],
        "prioridad": ticket["prioridad"],
        "proveedor": ticket["proveedor"],
        "historial": ticket["historial"][-HISTORY_LIMIT:],
    }


def _estados() -> dict[str, str]:
    """Overlay de estados. Se lee en cada llamada: otro proceso (el server MCP) lo escribe."""
    if not ESTADOS_PATH.exists():
        return {}
    return json.loads(ESTADOS_PATH.read_text(encoding="utf-8"))


def _guardar_estados(estados: dict[str, str]) -> None:
    """Escritura atómica: un archivo temporal y `os.replace`, nunca un JSON a medias."""
    ESTADOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ESTADOS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(estados, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, ESTADOS_PATH)


def actualizar_estado(args: ActualizarEstadoArgs) -> dict:
    """Fija el estado de un ticket. Idempotente: repetirla con el mismo estado no cambia nada."""
    from contracts.tool_schemas import tool_error  # Import local: tool_schemas importa este módulo.

    ticket = _tickets().get(args.ticket_id)
    if ticket is None:
        return tool_error(
            "negocio",
            f"ticket {args.ticket_id} no existe",
            sugerencia="Confirma el ID con el usuario antes de reintentar.",
        )
    estados = _estados()
    anterior = estados.get(args.ticket_id, ticket["estado"])
    cambio = anterior != args.estado
    if cambio:
        estados[args.ticket_id] = args.estado
        _guardar_estados(estados)
    return {"id": args.ticket_id, "estado": args.estado, "cambio": cambio}


def _texto(texto: str) -> str:
    """Minúsculas, sin acentos ni puntuación, y con los espacios colapsados.

    Un punto, una coma o un signo al final no cambian la llave: "mañana." y "mañana" son lo mismo.
    """
    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    sin_puntuacion = "".join(
        " " if unicodedata.category(caracter).startswith("P") else caracter
        for caracter in sin_acentos
    )
    return " ".join(sin_puntuacion.lower().split())


def idempotency_key(asunto: str, descripcion: str, request_id: str) -> str:
    payload = f"{_texto(asunto)}\n{_texto(descripcion)}\n{request_id}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _nuevos() -> dict:
    if not NUEVOS_PATH.exists():
        return {"claves": {}, "tickets": []}
    return json.loads(NUEVOS_PATH.read_text(encoding="utf-8"))


def _guardar_nuevos(data: dict) -> None:
    NUEVOS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = NUEVOS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, NUEVOS_PATH)


def _siguiente_id(tickets: list[dict]) -> str:
    usados = {ticket["id"] for ticket in tickets}
    numero = NUEVO_ID_INICIO
    while f"T-{numero}" in usados:
        numero += 1
    return f"T-{numero}"


def crear_ticket(args: CrearTicketArgs) -> dict:
    from agents.tools import current_request_id

    clave = idempotency_key(args.asunto, args.descripcion, current_request_id.get())
    data = _nuevos()
    existente = data["claves"].get(clave)
    if existente:
        guardado = next(ticket for ticket in data["tickets"] if ticket["id"] == existente)
        return {**guardado, "creado": False}
    ticket = {
        "id": _siguiente_id(data["tickets"]),
        "asunto": args.asunto.strip(),
        "descripcion": args.descripcion.strip(),
        "prioridad": args.prioridad,
    }
    data["claves"][clave] = ticket["id"]
    data["tickets"].append(ticket)
    _guardar_nuevos(data)
    return {**ticket, "creado": True}


def _normalizar(texto: str) -> str:
    """Minúsculas, sin acentos ni espacios alrededor: "  PAGOSRED " == "pagosred"."""
    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_acentos.lower().strip()


def listar_tickets_por_proveedor(args: ListarTicketsPorProveedorArgs) -> dict:
    """Tickets de un proveedor (máximo 10) y el total encontrado. Solo lectura."""
    buscado = _normalizar(args.nombre)
    estados = _estados()
    encontrados = [
        ticket
        for ticket in _tickets().values()
        if ticket["proveedor"] and _normalizar(ticket["proveedor"]) == buscado
    ]
    return {
        "proveedor": args.nombre,
        "total": len(encontrados),
        "tickets": [
            {
                "id": ticket["id"],
                "asunto": ticket["asunto"],
                "estado": estados.get(ticket["id"], ticket["estado"]),
            }
            for ticket in encontrados[:LISTA_LIMITE]
        ],
    }

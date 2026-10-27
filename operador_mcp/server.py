"""Server MCP del Agente Operador: las herramientas locales, expuestas por stdio.

    uv run python -m operador_mcp.server
    npx @modelcontextprotocol/inspector uv run python -m operador_mcp.server

Cada tool delega en un `ToolRunner`, así que el permiso, el tope de llamadas, el timeout y
la validación de argumentos son los mismos que en local. El rol sale de `OPERADOR_ROL`
(por defecto, "lectura"). La descripción de cada tool es la de su `ToolSpec` y las
annotations salen de su `ToolPolicy`.

Nada de este módulo escribe en stdout: en stdio, stdout es el canal del protocolo.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

import contracts.tool_schemas as schemas
from agents.tools import ToolRunner
from contracts.tool_schemas import ToolSpec

SPECS: list[ToolSpec] = [
    schemas.BUSCAR_TICKET,
    schemas.ACTUALIZAR_ESTADO,
    schemas.LISTAR_TICKETS_POR_PROVEEDOR,
]
if hasattr(schemas, "CONSULTAR_PROVEEDOR"):  # Solo si la base ya tiene el reto de la Clase 3.
    SPECS.append(schemas.CONSULTAR_PROVEEDOR)

runner = ToolRunner(SPECS, rol=os.environ.get("OPERADOR_ROL", "lectura"))
mcp = MCPServer("agente-operador")


def annotations_for(name: str) -> ToolAnnotations:
    """Hints para el cliente según la política. Son pistas, no seguridad: quien decide
    es el `ToolRunner`."""
    politica = runner.politica(name)
    return ToolAnnotations(
        read_only_hint=politica.permiso == "lectura",
        destructive_hint=politica.permiso == "peligrosa",
        idempotent_hint=politica.idempotente,
    )


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    """Ejecuta con el runner. Un `tool_error` se lanza como `ToolError`: el cliente lo
    recibe con `isError: true` y el JSON del error como texto."""
    result = runner.call(name, arguments)
    if "error" in result:
        raise ToolError(json.dumps(result, ensure_ascii=False))
    return result


def buscar_ticket(ticket_id: str) -> dict[str, Any]:
    return _call("buscar_ticket", ticket_id=ticket_id)


def actualizar_estado(ticket_id: str, estado: str) -> dict[str, Any]:
    return _call("actualizar_estado", ticket_id=ticket_id, estado=estado)


def consultar_proveedor(nombre: str) -> dict[str, Any]:
    return _call("consultar_proveedor", nombre=nombre)


def listar_tickets_por_proveedor(nombre: str) -> dict[str, Any]:
    return _call("listar_tickets_por_proveedor", nombre=nombre)


FUNCTIONS: dict[str, Callable[..., dict[str, Any]]] = {
    "buscar_ticket": buscar_ticket,
    "actualizar_estado": actualizar_estado,
    "consultar_proveedor": consultar_proveedor,
    "listar_tickets_por_proveedor": listar_tickets_por_proveedor,
}

for spec in SPECS:
    mcp.add_tool(
        FUNCTIONS[spec.name],
        name=spec.name,
        description=spec.description,
        annotations=annotations_for(spec.name),
    )


if __name__ == "__main__":
    mcp.run()

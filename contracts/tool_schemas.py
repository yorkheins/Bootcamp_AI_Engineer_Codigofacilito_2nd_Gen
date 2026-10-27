"""Contratos de las herramientas: qué recibe cada una y cómo se le describe al modelo."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from contracts.structured_output import sanitize_schema
from tools.proveedores import consultar_proveedor
from tools.tickets import actualizar_estado, buscar_ticket, listar_tickets_por_proveedor

ErrorTipo = Literal["validacion", "negocio", "transitorio", "permanente", "permiso", "limite"]
EstadoTicket = Literal["abierto", "en revisión", "esperando proveedor", "resuelto"]


def tool_error(
    tipo: ErrorTipo, mensaje: str, *, reintentable: bool = False, sugerencia: str | None = None
) -> dict:
    """Error de herramienta con forma fija, para que el modelo sepa qué hacer después.

    `reintentable` dice si repetir la misma llamada podría funcionar; `sugerencia`, qué
    probar en su lugar.
    """
    return {
        "error": {
            "tipo": tipo,
            "mensaje": mensaje,
            "reintentable": reintentable,
            "sugerencia": sugerencia,
        }
    }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel], dict]

    def to_openai(self) -> dict:
        """Definición de la herramienta en el formato `tools` de la API compatible con OpenAI.

        `parameters` sale del modelo Pydantic, sin `pattern`, `maxLength`, etc. (ver
        `sanitize_schema`): esas reglas se validan localmente en `execute_tool`.
        """
        parameters = sanitize_schema(self.args_model.model_json_schema(), strict=False)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


class BuscarTicketArgs(BaseModel):
    ticket_id: str = Field(pattern=r"^T-\d{4}$")


class ActualizarEstadoArgs(BaseModel):
    ticket_id: str = Field(pattern=r"^T-\d{4}$")
    estado: EstadoTicket


class ListarTicketsPorProveedorArgs(BaseModel):
    nombre: str = Field(min_length=2, description="Nombre del proveedor, por ejemplo PagosRed")


BUSCAR_TICKET = ToolSpec(
    name="buscar_ticket",
    description=(
        "Obtiene estado e historial de un ticket por su ID. "
        "Úsala solo si el usuario menciona un ID."
    ),
    args_model=BuscarTicketArgs,
    handler=buscar_ticket,
)


class ConsultarProveedorArgs(BaseModel):
    nombre: str = Field(description="Nombre del proveedor, por ejemplo PagosRed.")


CONSULTAR_PROVEEDOR = ToolSpec(
    name="consultar_proveedor",
    description=(
        "Consulta servicio, SLA en horas, estado y contacto de un proveedor por su nombre. "
        "Úsala cuando pregunten por un proveedor o cuando buscar_ticket haya devuelto su nombre. "
        "No la uses para buscar tickets ni su historial, ni si todavía no conoces el nombre."
    ),
    args_model=ConsultarProveedorArgs,
    handler=consultar_proveedor,
)

ACTUALIZAR_ESTADO = ToolSpec(
    name="actualizar_estado",
    description=(
        "Fija el estado de un ticket por su ID (abierto, en revisión, esperando proveedor o "
        "resuelto). Fija el valor indicado, no lo alterna: repetir la llamada no cambia nada. "
        "No la uses si el usuario no pidió un cambio de estado explícito."
    ),
    args_model=ActualizarEstadoArgs,
    handler=actualizar_estado,
)

LISTAR_TICKETS_POR_PROVEEDOR = ToolSpec(
    name="listar_tickets_por_proveedor",
    description=(
        "Lista los tickets de un proveedor por su nombre: id, asunto y estado, máximo 10, "
        "más el total encontrado. Solo lectura. Úsala cuando pregunten qué tickets tiene un "
        "proveedor; para el detalle de un ticket usa buscar_ticket."
    ),
    args_model=ListarTicketsPorProveedorArgs,
    handler=listar_tickets_por_proveedor,
)

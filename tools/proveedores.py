"""Herramienta `consultar_proveedor` sobre data/proveedores.json."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contracts.tool_schemas import ConsultarProveedorArgs

# Ruta fija: nunca se construye a partir de los argumentos del modelo.
PROVEEDORES_PATH = Path(__file__).resolve().parents[1] / "data" / "proveedores.json"

@lru_cache(maxsize=1)
def _proveedores() -> tuple[dict, ...]:
    proveedores = json.loads(PROVEEDORES_PATH.read_text(encoding="utf-8"))
    return tuple(proveedores)


def consultar_proveedor(args: ConsultarProveedorArgs) -> dict:
    """Servicio, SLA, estado y contacto de un proveedor del catálogo."""
    nombre = args.nombre.strip()
    if not nombre:
        return {"error": "falta el nombre del proveedor"}
    clave = nombre.casefold()
    for proveedor in _proveedores():
        if proveedor["nombre"].casefold() == clave:
            return {
                "nombre": proveedor["nombre"],
                "servicio": proveedor["servicio"],
                "sla_horas": proveedor["sla_horas"],
                "estado": proveedor["estado"],
                "contacto": proveedor["contacto"],
                "contratos_vigentes": proveedor["contratos_vigentes"],
            }
    return {"error": f"proveedor {nombre} no existe"}

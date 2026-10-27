"""Respuestas falsas para los tests de agentes: AgentStep y Plan serializados."""

from __future__ import annotations

import json

from core.llm_client import LLMResponse


def reply(payload: dict, *, tokens_in: int = 10, tokens_out: int = 5, cost: float = 0.0001):
    return LLMResponse(
        text=json.dumps(payload, ensure_ascii=False),
        provider="gemini",
        model="fake-model",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=12.0,
        cost_usd=cost,
    )


def use_tool(name: str = "buscar_ticket", **args) -> dict:
    return {
        "pensamiento": f"Necesito {name}.",
        "accion": "usar_herramienta",
        "herramienta": name,
        "argumentos_json": json.dumps(args or {"ticket_id": "T-1042"}),
        "respuesta": None,
    }


def answer(text: str = "Listo.") -> dict:
    return {
        "pensamiento": "Ya tengo lo necesario.",
        "accion": "responder",
        "herramienta": None,
        "argumentos_json": None,
        "respuesta": text,
    }


def plan(*pasos: tuple[str, str | None], razonamiento: str = "Plan corto.") -> dict:
    return {
        "razonamiento": razonamiento,
        "pasos": [{"objetivo": o, "herramienta": h} for o, h in pasos],
    }


class FakeClock:
    """Reloj que avanza `step` segundos cada vez que se consulta."""

    def __init__(self, step: float = 0.0) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value

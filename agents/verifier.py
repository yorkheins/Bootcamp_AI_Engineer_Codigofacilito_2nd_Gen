from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, model_validator

from agents.react_agent import COMPANY, PROMPTS_DIR, format_observation
from contracts.structured_output import generate_structured
from prompting import PromptKit

if TYPE_CHECKING:
    from agents.trace import TraceStep
    from core.llm_client import LLMClient


class Verificacion(BaseModel):
    problemas: list[str]
    aprobada: bool

    @model_validator(mode="after")
    def _coherente(self) -> Verificacion:
        if self.aprobada and self.problemas:
            raise ValueError("Si hay problemas, aprobada debe ser false.")
        if not self.aprobada and not self.problemas:
            raise ValueError("Si no está aprobada, explica al menos un problema.")
        return self


@dataclass(frozen=True)
class VerifiedAnswer:
    respuesta: str
    verificacion: Verificacion
    corregida: bool


def _observations(traza: list[TraceStep]) -> str:
    if not traza:
        return "(el agente no usó herramientas)"
    return "\n".join(
        f"{step.paso.herramienta} {step.paso.argumentos_json or '{}'} -> "
        f"{format_observation(step.observacion)}"
        for step in traza
    )


def verify_and_revise(
    client: LLMClient, pregunta: str, respuesta: str, traza: list[TraceStep]
) -> VerifiedAnswer:
    """Verifica la respuesta y, si no aprueba, la corrige **una sola vez**."""
    kit = PromptKit(PROMPTS_DIR)
    observaciones = _observations(traza)

    verifier = kit.get("verifier")
    verificacion = generate_structured(
        client,
        verifier.render(
            company=COMPANY, pregunta=pregunta, respuesta=respuesta, observaciones=observaciones
        ),
        Verificacion,
        log_extra={"prompt_name": verifier.name, "prompt_version": verifier.version},
        **verifier.model_params,
    )
    if verificacion.aprobada:
        return VerifiedAnswer(respuesta, verificacion, corregida=False)

    reviser = kit.get("reviser")
    revised = client.generate(
        reviser.render(
            company=COMPANY,
            pregunta=pregunta,
            respuesta=respuesta,
            problemas="\n".join(f"- {p}" for p in verificacion.problemas),
            observaciones=observaciones,
        ),
        log_extra={"prompt_name": reviser.name, "prompt_version": reviser.version},
        **reviser.model_params,
    )
    return VerifiedAnswer(revised.text.strip(), verificacion, corregida=True)

"""Compara tool loop (Clase 3), ReAct y plan-and-execute con las mismas preguntas.

Uso:
    uv run scripts/compare_agents.py
    uv run scripts/compare_agents.py --repeat 3

Usa solo el proveedor primario, sin fallback. Las herramientas son las de `TOOLS` en
main.py: las preguntas que necesitan una herramienta que no está se omiten con una nota.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # Para importar los paquetes del repo al correr como script.

from agents import PlanAndExecute, ReActAgent  # noqa: E402
from contracts import MaxIterationsError, run_tool_loop  # noqa: E402
from core.config import Settings  # noqa: E402
from core.errors import AllProvidersFailedError  # noqa: E402
from core.llm_client import LLMClient  # noqa: E402
from main import TOOLS  # noqa: E402
from prompting import PromptKit  # noqa: E402

MODES = ("tool_loop", "react", "plan")
BATCH_MAX_RETRIES = 6  # Igual que compare_prompts: aguanta el límite por minuto del free tier.

# De menos a más pasos. (etiqueta, pregunta, herramientas que necesita)
QUESTIONS = [
    ("un ticket", "¿Cómo va el T-1042?", {"buscar_ticket"}),
    (
        "dos tickets",
        "Compara el estado del T-1042 y el T-1014: ¿cuál va más avanzado?",
        {"buscar_ticket"},
    ),
    (
        "ticket + proveedor",
        "¿El T-1042 depende de algún proveedor? ¿Cuál es su SLA?",
        {"buscar_ticket", "consultar_proveedor"},
    ),
    ("inexistente", "¿Cómo va el T-9999?", {"buscar_ticket"}),
]


@dataclass
class Run:
    motivo: str
    llamadas: int
    tokens: int
    cost_usd: float
    latency_ms: float


def run_once(mode: str, client: LLMClient, question: str) -> Run:
    """Una ejecución medida con `client.track()`."""
    with client.track() as usage:
        if mode == "tool_loop":
            prompt = PromptKit(ROOT / "prompts").get("operator_tools")
            messages = prompt.render(company="Acme Operaciones", message=question)
            try:
                run_tool_loop(client, messages, TOOLS, **prompt.model_params)
                motivo = "respuesta"
            except MaxIterationsError:
                motivo = "max_pasos"
            except AllProvidersFailedError:
                motivo = "error"
        else:
            agent_cls = ReActAgent if mode == "react" else PlanAndExecute
            motivo = agent_cls(client, TOOLS).run(question).motivo
    return Run(motivo, usage.calls, usage.tokens, usage.cost_usd, usage.latency_ms)


def average(runs: list[Run]) -> Run:
    n = len(runs)
    counts = Counter(r.motivo for r in runs)
    motivo = ", ".join(m if c == n else f"{m}×{c}" for m, c in counts.most_common())
    return Run(
        motivo=motivo,
        llamadas=round(sum(r.llamadas for r in runs) / n),
        tokens=round(sum(r.tokens for r in runs) / n),
        cost_usd=sum(r.cost_usd for r in runs) / n,
        latency_ms=sum(r.latency_ms for r in runs) / n,
    )


def format_table(rows: list[tuple[str, str, Run]]) -> str:
    headers = ["modo", "pregunta", "motivo", "llamadas", "tokens", "costo", "latencia"]
    cells = [
        [
            mode,
            label,
            run.motivo,
            str(run.llamadas),
            f"{run.tokens:,}",
            f"${run.cost_usd:.5f}",
            f"{run.latency_ms / 1000:.1f} s",
        ]
        for mode, label, run in rows
    ]
    widths = [max(len(r[i]) for r in [headers, *cells]) for i in range(len(headers))]
    lines = [" | ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)) for r in cells]
    header = " | ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))
    return "\n".join([header, "-+-".join("-" * w for w in widths), *lines])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compara tool loop, ReAct y plan-and-execute.")
    parser.add_argument("--repeat", type=int, default=1, help="Ejecuciones por pregunta.")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat debe ser 1 o más.")

    try:
        client = LLMClient.from_settings(Settings(fallbacks=[], max_retries=BATCH_MAX_RETRIES))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    available = {tool.name for tool in TOOLS}
    rows: list[tuple[str, str, Run]] = []
    for label, question, needed in QUESTIONS:
        missing = needed - available
        if missing:
            print(f"Nota: se omite «{label}»: falta {', '.join(sorted(missing))} en TOOLS.")
            continue
        for mode in MODES:
            print(f"{mode} · {label} ", end="", file=sys.stderr, flush=True)
            runs = []
            for _ in range(args.repeat):
                runs.append(run_once(mode, client, question))
                print(".", end="", file=sys.stderr, flush=True)
            print(file=sys.stderr)
            rows.append((mode, label, average(runs)))

    print()
    print(format_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())

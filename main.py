"""CLI del Agente Operador.

Uso:
    uv run main.py "Resume este ticket: el portal de proveedores no carga desde las 9:00"
    uv run main.py --provider groq "hola"
    uv run main.py --demo
    uv run main.py --prompt ticket_classifier --ticket T-1099
    uv run main.py --prompt ticket_classifier --prompt-version 1 "No abre el ERP"
    uv run main.py --structured --ticket T-1042
    uv run main.py --tools "¿Cómo va el T-1042?"
    uv run main.py --agent react "Compara el T-1042 y el T-1014"
    uv run main.py --agent plan "Compara el T-1042 y el T-1014"
    uv run main.py --agent react --mcp "¿Cómo va el T-1042?"
    uv run main.py --agent react --rol lectura "Marca el T-1042 como resuelto"
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from pydantic import ValidationError

from contracts import (
    BUSCAR_TICKET,
    CONSULTAR_PROVEEDOR,
    MaxIterationsError,
    StructuredOutputError,
    TicketClassification,
    generate_structured,
    run_tool_loop,
)
from core.config import KNOWN_PROVIDERS, Settings, get_settings
from core.errors import AllProvidersFailedError
from core.llm_client import LLMClient, ToolCall
from prompting import PromptKit, PromptNotFoundError

# --demo: solo Gemini, sin fallbacks. Funciona aunque GroqProvider siga con su TODO.
DEMO_TICKET_ID = "T-1043"
TICKETS_PATH = Path(__file__).parent / "data" / "tickets_ejemplo.jsonl"

DATA_DIR = Path(__file__).parent / "data"
PROMPTS_DIR = Path(__file__).parent / "prompts"
# Variables fijas por prompt; `ticket` sale de --ticket o del texto de la línea de comandos.
PROMPT_VARIABLES = {
    "ticket_classifier": {"company": "Acme Operaciones"},
    "ticket_triage": {"company": "Acme Operaciones"},
    "operator_tools": {"company": "Acme Operaciones"},
}
TICKETS_DB_PATH = DATA_DIR / "tickets_db.json"
# Clase 5: nombre, permiso, resultado e intentos de cada herramienta (nunca argumentos).
TOOL_LOG_PATH = Path(__file__).parent / "logs" / "tool_calls.jsonl"

# Herramientas disponibles en el modo --tools.
TOOLS = [BUSCAR_TICKET, CONSULTAR_PROVEEDOR]
TOOL_RESULT_PREVIEW = 120  # Caracteres que se muestran de cada resultado en pantalla.

GRAY = "\033[90m"
RESET = "\033[0m"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agente Operador: pregunta algo al LLM.")
    parser.add_argument(
        "prompt",
        nargs="?",
        help=f"Texto a enviar al modelo (con --demo, por defecto el ticket {DEMO_TICKET_ID}).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--provider",
        choices=KNOWN_PROVIDERS,
        help="Fuerza un proveedor y desactiva los fallbacks.",
    )
    mode.add_argument(
        "--demo",
        action="store_true",
        help="Demo de la clase: usa solo Gemini, sin fallbacks (no necesita Groq).",
    )
    parser.add_argument(
        "--prompt",
        dest="prompt_name",
        metavar="NOMBRE",
        help="Usa un prompt versionado de prompts/ (versión activa por defecto).",
    )
    parser.add_argument(
        "--prompt-version", type=int, metavar="N", help="Versión del prompt (requiere --prompt)."
    )
    parser.add_argument(
        "--ticket",
        metavar="ID",
        help="Usa la descripción de un ticket de data/*.jsonl o data/tickets_db.json.",
    )
    parser.add_argument(
        "--structured",
        action="store_true",
        help="Clasifica el ticket y regresa un TicketClassification en JSON (Clase 3).",
    )
    parser.add_argument(
        "--tools",
        action="store_true",
        help="Responde con herramientas: buscar_ticket y consultar_proveedor (Clase 3).",
    )
    parser.add_argument(
        "--agent",
        choices=("react", "plan"),
        help="Responde con un agente ReAct o plan-and-execute (Clase 4).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Con --agent, muestra pensamientos y observaciones completas.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Con --agent, verifica la respuesta contra la traza y la corrige una vez.",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Con --agent, toma las herramientas del server MCP por stdio (Clase 5).",
    )
    parser.add_argument(
        "--rol",
        choices=("lectura", "operador"),
        default="operador",
        help="Con --agent, rol para el ToolRunner: lectura u operador (Clase 5).",
    )
    args = parser.parse_args(argv)
    if args.verify and not args.agent:
        parser.error("--verify requiere --agent.")
    if args.mcp and not args.agent:
        parser.error("--mcp requiere --agent.")
    if args.agent and (args.structured or args.tools or args.prompt_name or args.demo):
        parser.error("--agent no se combina con --structured, --tools, --prompt ni --demo.")
    if args.agent and (args.prompt is None or args.ticket):
        parser.error('--agent necesita la pregunta como texto, por ejemplo "¿Cómo va el T-1042?".')
    if args.prompt is None and not (args.demo or args.ticket):
        parser.error("falta el texto a enviar (o usa --demo o --ticket).")
    if args.prompt_version is not None and not args.prompt_name:
        parser.error("--prompt-version requiere --prompt.")
    if args.structured and args.tools:
        parser.error("--structured y --tools no se pueden usar juntos.")
    if (args.structured or args.tools) and (args.prompt_name or args.demo):
        parser.error("--structured y --tools usan su propio prompt: quita --prompt o --demo.")
    if args.tools and (args.prompt is None or args.ticket):
        parser.error('--tools necesita la pregunta como texto, por ejemplo "¿Cómo va el T-1042?".')
    return args


def find_ticket(ticket_id: str) -> dict:
    """Busca un ticket por `id` en todos los data/*.jsonl y en data/tickets_db.json."""
    for path in sorted(DATA_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and (ticket := json.loads(line)).get("id") == ticket_id:
                return ticket
    for ticket in json.loads(TICKETS_DB_PATH.read_text(encoding="utf-8")):
        if ticket["id"] == ticket_id:
            return ticket
    raise LookupError(f"No encontré el ticket {ticket_id} en {DATA_DIR}.")


def print_tool_event(kind: str, call: ToolCall, result: dict | None) -> None:
    """Muestra el ciclo de herramientas: → lo que pide el modelo, ← lo que regresa."""
    if kind == "call":
        print(f"{GRAY}→ {call.name} {call.arguments}{RESET}")
        return
    content = result["content"] if result else ""
    if len(content) > TOOL_RESULT_PREVIEW:
        content = content[: TOOL_RESULT_PREVIEW - 1] + "…"
    print(f"{GRAY}← {content}{RESET}")


def run_structured(client: LLMClient, text: str) -> int:
    prompt = PromptKit(PROMPTS_DIR).get("ticket_triage")
    messages = prompt.render(**PROMPT_VARIABLES[prompt.name], ticket=text)
    log_extra = {"prompt_name": prompt.name, "prompt_version": prompt.version}
    result = generate_structured(
        client, messages, TicketClassification, log_extra=log_extra, **prompt.model_params
    )
    print(result.model_dump_json(indent=2))
    return 0


def run_tools(client: LLMClient, question: str) -> int:
    prompt = PromptKit(PROMPTS_DIR).get("operator_tools")
    messages = prompt.render(**PROMPT_VARIABLES[prompt.name], message=question)
    log_extra = {"prompt_name": prompt.name, "prompt_version": prompt.version}
    answer = run_tool_loop(
        client,
        messages,
        TOOLS,
        log_extra=log_extra,
        on_event=print_tool_event,
        **prompt.model_params,
    )
    print(answer.strip())
    return 0


STOP_MESSAGES = {
    "max_pasos": "el agente llegó al máximo de pasos sin responder.",
    "presupuesto": "el agente agotó su presupuesto de tokens o de costo.",
    "sin_progreso": "el agente repitió la misma acción sin avanzar.",
    "timeout": "el agente se quedó sin tiempo.",
    "error": "el modelo no produjo un paso válido o el proveedor falló.",
}


def describe_action(paso) -> str:
    """`buscar_ticket T-1042` o `responder`, para la línea de pasos."""
    if paso.accion == "responder":
        return "responder"
    try:
        values = json.loads(paso.argumentos_json or "{}").values()
    except (ValueError, AttributeError):
        values = [paso.argumentos_json]
    return " ".join([paso.herramienta or "?", *(str(v) for v in values)]).strip()


def run_agent(
    client: LLMClient,
    kind: str,
    question: str,
    verbose: bool,
    verify: bool = False,
    *,
    mcp: bool = False,
    rol: str = "operador",
) -> int:
    from agents import PlanAndExecute, ReActAgent, verify_and_revise
    from agents.tools import ToolRunner
    from contracts.tool_schemas import ACTUALIZAR_ESTADO, CREAR_TICKET, LISTAR_TICKETS_POR_PROVEEDOR

    actions: list[str] = []

    def on_event(event: str, data: dict) -> None:
        if event == "plan":
            plan = data["plan"]
            label = "replan" if data["replan"] else "plan"
            print(f"{label}: {len(plan.pasos)} pasos")
            for i, paso in enumerate(plan.pasos, start=1):
                tool = f" ({paso.herramienta})" if paso.herramienta else ""
                print(f"  {i}. {paso.objetivo}{tool}")
        elif event == "paso":
            paso = data["paso"]
            actions.append(f"[{len(actions) + 1}] {describe_action(paso)}")
            if verbose:
                print(f"{GRAY}{actions[-1]} · {paso.pensamiento}{RESET}")
        elif event == "observacion" and verbose:
            print(f"{GRAY}    ← {json.dumps(data['observacion'], ensure_ascii=False)}{RESET}")

    agent_cls = ReActAgent if kind == "react" else PlanAndExecute
    if mcp:
        # El server MCP aplica su propio ToolRunner con OPERADOR_ROL.
        from operador_mcp.client import MCPToolset

        with MCPToolset(rol=rol) as toolset:
            print(f"{GRAY}herramientas: MCP por stdio · rol {rol}{RESET}")
            result = agent_cls(client, toolset.tools(), on_event=on_event).run(question)
    else:
        # El agente puede proponer actualizar_estado; el ToolRunner decide según el rol.
        extras = [ACTUALIZAR_ESTADO, LISTAR_TICKETS_POR_PROVEEDOR, CREAR_TICKET]
        tools = [*TOOLS, *(tool for tool in extras if tool not in TOOLS)]
        runner = ToolRunner(tools, rol=rol, log_path=TOOL_LOG_PATH, request_id=uuid.uuid4().hex)
        print(f"{GRAY}herramientas: locales · rol {rol}{RESET}")
        result = agent_cls(client, tools, on_event=on_event, runner=runner).run(question)
    respuesta, checked = result.respuesta, None
    llamadas, tokens, cost_usd = result.llamadas, result.tokens, result.cost_usd
    if verify and respuesta:
        with client.track() as extra:
            checked = verify_and_revise(client, question, respuesta, result.traza)
        respuesta = checked.respuesta
        llamadas += extra.calls
        tokens += extra.tokens
        cost_usd += extra.cost_usd

    if actions:
        print("   ".join(actions))
    if checked:
        if checked.corregida:
            problems = "; ".join(checked.verificacion.problemas)
            print(f"{GRAY}verificación: no aprobada ({problems}) → respuesta corregida{RESET}")
        else:
            print(f"{GRAY}verificación: aprobada{RESET}")
    if respuesta:
        print(respuesta.strip())
    print(
        f"{GRAY}motivo: {result.motivo} · {llamadas} llamadas · "
        f"{tokens:,} tokens · ${cost_usd:.6f}{RESET}"
    )
    if result.motivo != "respuesta":
        print(f"Aviso: {STOP_MESSAGES[result.motivo]}", file=sys.stderr)
        return 2
    return 0


def demo_prompt() -> str:
    """Arma el prompt de la demo a partir del ticket de ejemplo."""
    for line in TICKETS_PATH.read_text(encoding="utf-8").splitlines():
        ticket = json.loads(line)
        if ticket["id"] == DEMO_TICKET_ID:
            return f"Resume este ticket: {ticket['asunto']}. {ticket['descripcion']}"
    raise LookupError(f"No encontré el ticket {DEMO_TICKET_ID} en {TICKETS_PATH}")


def fail(message: str) -> int:
    print(f"Error: {message}", file=sys.stderr)
    return 1


def config_error(e: ValidationError) -> str:
    details = "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
    return f"configuración inválida. {details}"


def build_client(args: argparse.Namespace) -> LLMClient:
    """Arma el cliente. Con --prompt o --ticket, si la cadena de fallbacks no se puede
    construir (por ejemplo, GroqProvider pendiente), sigue solo con el primario."""
    if args.demo:
        return LLMClient.from_settings(Settings(primary="gemini", fallbacks=[]))
    if args.provider:
        return LLMClient.from_settings(Settings(primary=args.provider, fallbacks=[]))
    if not (args.prompt_name or args.ticket):
        return LLMClient.from_settings(get_settings())
    try:
        return LLMClient.from_settings(get_settings())
    except (ValidationError, NotImplementedError) as e:
        reason = config_error(e) if isinstance(e, ValidationError) else str(e)
        settings = Settings(fallbacks=[])
        print(
            f"{GRAY}Aviso: se usa solo {settings.primary}, sin fallback ({reason}){RESET}",
            file=sys.stderr,
        )
        return LLMClient.from_settings(settings)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        text = find_ticket(args.ticket)["descripcion"] if args.ticket else args.prompt
    except LookupError as e:
        return fail(str(e))

    params: dict = {}
    log_extra: dict = {}
    if args.prompt_name:
        try:
            prompt = PromptKit(PROMPTS_DIR).get(args.prompt_name, args.prompt_version)
            variables = PROMPT_VARIABLES.get(prompt.name, {})
            messages = prompt.render(**variables, ticket=text)
        except (PromptNotFoundError, ValueError) as e:
            return fail(str(e))
        cli_params: dict = {}  # Flags de la CLI (si existen) tienen prioridad.
        params = {**prompt.model_params, **cli_params}
        log_extra = {"prompt_name": prompt.name, "prompt_version": prompt.version}
    else:
        try:
            prompt = PromptKit(PROMPTS_DIR).get("operator_assistant")
            messages = prompt.render(message=text or demo_prompt())
        except (PromptNotFoundError, ValueError) as e:
            return fail(str(e))
        params = dict(prompt.model_params)
        log_extra = {"prompt_name": prompt.name, "prompt_version": prompt.version}

    try:
        client = build_client(args)
    except ValidationError as e:
        return fail(config_error(e))
    except NotImplementedError as e:
        return fail(str(e))

    if args.agent:
        return run_agent(
            client, args.agent, text, args.verbose, args.verify, mcp=args.mcp, rol=args.rol
        )

    if args.structured or args.tools:
        try:
            return run_structured(client, text) if args.structured else run_tools(client, text)
        except StructuredOutputError as e:
            return fail(f"el modelo no devolvió un ticket válido. {e}")
        except MaxIterationsError as e:
            return fail(f"no hubo respuesta final. {e}")
        except AllProvidersFailedError as e:
            lines = "\n".join(f"  - {type(err).__name__}: {err}" for err in e.errors)
            return fail(f"ningún proveedor pudo responder.\n{lines}")

    try:
        response = client.generate(messages, log_extra=log_extra, **params)
    except AllProvidersFailedError as e:
        lines = "\n".join(f"  - {type(err).__name__}: {err}" for err in e.errors)
        return fail(f"ningún proveedor pudo responder.\n{lines}")

    print(response.text.strip())
    print(
        f"{GRAY}{response.provider} · {response.model} · "
        f"{response.tokens_in}/{response.tokens_out} tokens · "
        f"{response.latency_ms:.0f} ms · ${response.cost_usd:.6f}{RESET}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
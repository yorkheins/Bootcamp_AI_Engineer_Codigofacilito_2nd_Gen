"""Cliente MCP síncrono: lanza el server por stdio y expone sus tools como `ToolSpec`.

    with MCPToolset(rol="lectura") as toolset:
        agente = ReActAgent(client, toolset.tools())

El SDK es asíncrono y el agente no. `MCPToolset` abre la sesión en un hilo con su propio
event loop y le pasa las llamadas por una cola. La sesión se abre y se cierra dentro de
la misma tarea, como exige el SDK.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from mcp import Client, MCPError, StdioServerParameters, stdio_client
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import BaseModel, ConfigDict, create_model

from contracts.tool_schemas import ToolSpec, tool_error

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT_S = 30
# El stderr del server (sus logs) va a un archivo para no mezclarse con la salida de la CLI.
SERVER_LOG = ROOT / "logs" / "mcp_server.log"


def to_dict(result: CallToolResult) -> dict:
    """Respuesta de `call_tool` → dict. `isError` → `tool_error` con el mensaje del server."""
    text = "".join(block.text for block in result.content if isinstance(block, TextContent))
    if result.is_error:
        # Nuestro server manda el tool_error como JSON; el SDK le antepone
        # "Error executing tool X: ". Cualquier otro error se trata como permanente.
        start = text.find("{")
        if start >= 0:
            try:
                data = json.loads(text[start:])
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                return data
        return tool_error("permanente", text or "El server MCP regresó un error sin mensaje")
    if isinstance(result.structured_content, dict):
        return result.structured_content
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"resultado": text}
    return data if isinstance(data, dict) else {"resultado": data}


def _args_model(tool: Tool) -> type[BaseModel]:
    """Modelo permisivo: acepta cualquier argumento y publica el `inputSchema` del server.

    La validación real ocurre en el server, con su `ToolRunner`.
    """
    schema = dict(tool.input_schema)

    def use_server_schema(target: dict[str, Any]) -> None:
        target.clear()
        target.update(schema)

    config = ConfigDict(extra="allow", json_schema_extra=use_server_schema)
    return create_model(f"{tool.name}_args", __config__=config)


class MCPToolset:
    def __init__(
        self,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        *,
        rol: str = "lectura",
        timeout_s: float = DEFAULT_TIMEOUT_S,
        server_log: Path | None = SERVER_LOG,
    ) -> None:
        self.command = command or sys.executable
        self.args = args if args is not None else ["-m", "operador_mcp.server"]
        # El hijo no hereda el entorno por defecto (el SDK solo pasa PATH, HOME, ...).
        self.env = {**os.environ, **(env or {}), "OPERADOR_ROL": rol}
        self.timeout_s = timeout_s
        self.server_log = server_log
        self._tools: list[Tool] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> MCPToolset:
        ready: Future[None] = Future()
        self._thread = threading.Thread(target=asyncio.run, args=(self._serve(ready),), daemon=True)
        self._thread.start()
        try:
            ready.result(timeout=self.timeout_s)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        """Cierra la sesión y el subproceso, también si hubo una excepción."""
        if self._loop and self._queue and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)
        if self._thread:
            self._thread.join(timeout=self.timeout_s)

    async def _serve(self, ready: Future[None]) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        params = StdioServerParameters(
            command=self.command, args=self.args, env=self.env, cwd=str(ROOT)
        )
        errlog = sys.stderr
        if self.server_log:
            self.server_log.parent.mkdir(parents=True, exist_ok=True)
            errlog = self.server_log.open("a", encoding="utf-8")
        try:
            async with Client(stdio_client(params, errlog=errlog)) as client:
                self._tools = (await client.list_tools()).tools
                ready.set_result(None)
                while (item := await self._queue.get()) is not None:
                    name, arguments, future = item
                    try:
                        future.set_result(await client.call_tool(name, arguments))
                    except Exception as e:
                        future.set_exception(e)
        except BaseException as e:
            if not ready.done():
                ready.set_exception(e)
            else:
                raise
        finally:
            if errlog is not sys.stderr:
                errlog.close()

    def call(self, name: str, arguments: dict) -> dict:
        if not (self._loop and self._queue):
            raise RuntimeError("Usa MCPToolset dentro de un bloque with.")
        future: Future[CallToolResult] = Future()
        self._loop.call_soon_threadsafe(self._queue.put_nowait, (name, arguments, future))
        try:
            return to_dict(future.result(timeout=self.timeout_s))
        except TimeoutError:
            return tool_error("transitorio", f"{name} no respondió en {self.timeout_s} s")
        except MCPError as e:
            return tool_error("permanente", str(e))

    def tools(self) -> list[ToolSpec]:
        """Una `ToolSpec` por tool de `tools/list`: nombre, descripción e `inputSchema`."""
        return [
            ToolSpec(
                name=tool.name,
                description=tool.description or "",
                args_model=_args_model(tool),
                handler=lambda args, name=tool.name: self.call(name, args.model_dump()),
            )
            for tool in self._tools
        ]

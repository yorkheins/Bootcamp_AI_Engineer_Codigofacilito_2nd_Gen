"""Server MCP real: en memoria (Client(mcp) del SDK) y por stdio (MCPToolset). Sin red."""

from __future__ import annotations

import anyio
import pytest
from mcp import Client

from agents.tools import POLITICAS
from contracts.tool_schemas import ToolSpec
from operador_mcp.client import MCPToolset
from operador_mcp.server import SPECS, mcp

TIMEOUT_S = 20


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def toolset_factory(tmp_path):
    def factory(rol: str) -> MCPToolset:
        return MCPToolset(rol=rol, timeout_s=TIMEOUT_S, server_log=tmp_path / "mcp_server.log")

    return factory


@pytest.mark.anyio
async def test_tools_list_matches_specs_and_policies():
    with anyio.fail_after(TIMEOUT_S):
        async with Client(mcp) as client:
            listed = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(listed) == {spec.name for spec in SPECS}
    assert {"buscar_ticket", "actualizar_estado"} <= set(listed)
    for spec in SPECS:
        tool = listed[spec.name]
        politica = POLITICAS[spec.name]
        assert tool.description == spec.description
        assert set(tool.input_schema["properties"]) == set(spec.args_model.model_fields)
        assert tool.annotations.read_only_hint is (politica.permiso == "lectura")
        assert tool.annotations.destructive_hint is (politica.permiso == "peligrosa")
        assert tool.annotations.idempotent_hint is politica.idempotente


@pytest.mark.anyio
async def test_call_tool_returns_structured_content():
    with anyio.fail_after(TIMEOUT_S):
        async with Client(mcp) as client:
            result = await client.call_tool("buscar_ticket", {"ticket_id": "T-1042"})
    assert result.is_error is False
    assert result.structured_content["id"] == "T-1042"


def test_stdio_toolset_exposes_tool_specs(toolset_factory):
    with toolset_factory("lectura") as toolset:
        tools = {tool.name: tool for tool in toolset.tools()}
        assert all(isinstance(tool, ToolSpec) for tool in tools.values())
        assert tools["buscar_ticket"].description == SPECS[0].description
        parameters = tools["actualizar_estado"].to_openai()["function"]["parameters"]
        assert parameters["required"] == ["ticket_id", "estado"]
        result = toolset.call("buscar_ticket", {"ticket_id": "T-1042"})
    assert result["id"] == "T-1042"
    assert "historial" in result


def test_stdio_read_role_cannot_update_state(toolset_factory):
    with toolset_factory("lectura") as toolset:
        result = toolset.call("actualizar_estado", {"ticket_id": "T-1042", "estado": "resuelto"})
    assert result["error"]["tipo"] == "permiso"


def test_stdio_validation_and_unknown_tool_errors(toolset_factory):
    with toolset_factory("lectura") as toolset:
        invalid = toolset.call("buscar_ticket", {"ticket_id": "1042"})
        unknown = toolset.call("no_existe", {})
    assert invalid["error"]["tipo"] == "validacion"
    assert unknown["error"]["tipo"] == "permanente"


def test_toolset_closes_even_with_exception(toolset_factory):
    toolset = toolset_factory("lectura")
    with pytest.raises(RuntimeError), toolset:
        raise RuntimeError("falla dentro del with")
    assert not toolset._thread.is_alive()

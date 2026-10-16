"""O servidor sobe pelo entry point (`uv run mcp-freecad`) e fala MCP por stdio."""

import json
import os
import shutil
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_freecad.errors import FreecadNotFound
from mcp_freecad.freecad_cmd import resolve_cmd

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {"freecad_version", "doc_inspect", "doc_export", "doc_export_2d", "part_run_script"}

pytestmark = pytest.mark.skipif(shutil.which("uv") is None, reason="uv não encontrado")


def _session():
    # O SDK só repassa ao servidor um punhado de variáveis seguras: MCP_FREECAD_* vai à mão.
    env = {k: v for k, v in os.environ.items() if k.startswith("MCP_FREECAD_")}
    params = StdioServerParameters(
        command="uv", args=["--directory", str(ROOT), "run", "mcp-freecad"], env=env
    )
    return stdio_client(params)


async def test_server_lists_the_five_tools():
    with anyio.fail_after(60):
        async with _session() as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}

    assert set(tools) == TOOLS
    schema = {name: tool.input_schema for name, tool in tools.items()}
    assert schema["doc_inspect"]["required"] == ["path"]
    assert schema["doc_export"]["required"] == ["path", "out_path"]
    assert schema["doc_export_2d"]["required"] == ["path", "out_path"]
    assert schema["part_run_script"]["required"] == ["script", "out_dir"]
    props = schema["doc_export_2d"]["properties"]
    assert props["mode"]["enum"] == ["section", "projection"]
    assert props["axis"]["enum"] == ["x", "y", "z"]
    assert all(tool.description for tool in tools.values())


@pytest.mark.integration
async def test_tool_call_over_the_protocol():
    try:
        resolve_cmd()
    except FreecadNotFound:
        pytest.skip("FreeCAD (freecadcmd) não encontrado")
    with anyio.fail_after(60):
        async with _session() as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            ok = await session.call_tool("freecad_version", {})
            bad = await session.call_tool("doc_inspect", {"path": "/etc/passwd"})

    version = ok.structured_content or json.loads(ok.content[0].text)
    assert version["version"].startswith("1.")
    error = bad.structured_content or json.loads(bad.content[0].text)
    assert error["error"].startswith("[MF-PATH-1]")

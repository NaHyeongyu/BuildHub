from __future__ import annotations

import io
import json

import pytest

import mcp_server
from mcp_server import PromtyMCPServer, run_mcp_server


def _server() -> PromtyMCPServer:
    return PromtyMCPServer(
        api_url="https://api.example.test",
        token="collector-secret",
        project_id="eaf2b822-2787-4d83-a9a4-b2d743a28d06",
    )


def test_mcp_lists_read_only_context_tools() -> None:
    response = _server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response is not None
    tools = response["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["get_project_context"]
    assert all("untrusted reference data" in tool["description"] for tool in tools)
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)


def test_mcp_context_tool_returns_text_and_structured_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "available": True,
        "project": {"id": "project-id", "name": "Promty"},
        "memory": {"body_markdown": "# Project Memory", "warnings": []},
    }
    monkeypatch.setattr(mcp_server, "fetch_project_context", lambda **_kwargs: payload)

    response = _server().handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "get_project_context", "arguments": {}},
        }
    )

    assert response is not None
    assert response["result"]["structuredContent"] == payload
    assert "# Project Memory" in response["result"]["content"][0]["text"]


def test_mcp_stdio_uses_json_rpc_lines() -> None:
    stdin = io.StringIO(
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        + "\n"
    )
    stdout = io.StringIO()

    assert run_mcp_server(_server(), stdin=stdin, stdout=stdout) == 0

    response = json.loads(stdout.getvalue())
    assert response["id"] == 1
    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert response["result"]["capabilities"] == {"tools": {}}

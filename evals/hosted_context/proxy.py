"""Transparent stdio -> authenticated loopback MCP proxy with independent recording.

No tool selection, fixture lookup, prompting, or response rewriting happens here.
The same production tool descriptions/schema and initialization instructions are
forwarded. Controller credentials and raw responses never enter durable results.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlsplit

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from .instrumentation import MCPTraceRecorder


async def run(connection: Path, person: str, calls: Path, catalog: Path):
    config = json.loads(connection.read_text())
    parsed = urlsplit(config["url"])
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError("evaluation proxy accepts loopback disposable Gateway only")
    import httpx2
    async with httpx2.AsyncClient(headers={
        "Authorization": "Bearer " + config["tokens"][person]}, trust_env=False) as http:
        async with streamable_http_client(config["url"], http_client=http) as (read, write):
            async with ClientSession(read, write) as upstream:
                initialized = await upstream.initialize()
                tools = await upstream.list_tools()
                snapshot = {"instructions": initialized.instructions,
                            "tools": [t.model_dump(mode="json", by_alias=True) for t in tools.tools]}
                catalog.write_text(json.dumps(snapshot, indent=2))
                async def list_tools(ctx, params):
                    return tools

                async def call_tool(ctx, params):
                    name, arguments = params.name, params.arguments or {}
                    # Capture intent before awaiting the response; failures must
                    # remain visible rather than disappearing from recall.
                    index = sum(1 for _ in calls.open()) if calls.exists() else 0
                    with calls.open("a") as f:
                        f.write(json.dumps({"phase": "request", "index": index,
                            "name": name, "arguments": {k: v for k, v in arguments.items()
                            if k in ("limit", "detail", "since", "until", "query", "cursor")}}) + "\n")
                    result = await upstream.call_tool(name, arguments)
                    recorder = MCPTraceRecorder(case_id="transport", client="proxy",
                                                model="not-a-model", trial="transport")
                    recorder.record(name=name, arguments=arguments,
                                    response=result.model_dump(mode="json", by_alias=True))
                    with calls.open("a") as f:
                        f.write(json.dumps({"phase": "response", "index": index,
                            "call": recorder.calls[0], "is_error": bool(result.is_error)}) + "\n")
                    return result

                server = Server("OpenWorkGraph", instructions=initialized.instructions,
                                on_list_tools=list_tools, on_call_tool=call_tool)
                async with stdio_server() as (stdin, stdout):
                    await server.run(stdin, stdout, server.create_initialization_options())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--connection", type=Path, required=True)
    parser.add_argument("--person", choices=["populated", "empty", "local-only"], required=True)
    parser.add_argument("--calls", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.connection, args.person, args.calls, args.catalog))

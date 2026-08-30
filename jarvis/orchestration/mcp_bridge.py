"""Authenticated loopback Streamable HTTP MCP bridge to the existing Jarvis core."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time

log = logging.getLogger(__name__)


class MCPBridge:
    """Expose only the supplied core's tools; no second task manager or tool executor."""

    def __init__(self, core):
        self.core = core
        self.token = secrets.token_urlsafe(32)
        self.server = None
        self.connections = set()
        self.url = ""

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    async def __aenter__(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=65536)
        port = self.server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()
        pending = list(self.connections)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self.connections.add(task)
        try:
            async with asyncio.timeout(240):
                head = await reader.readuntil(b"\r\n\r\n")
                lines = head.decode("ascii").split("\r\n")
                method, path, _ = lines[0].split()
                headers = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
                headers = {key.lower(): value.strip() for key, value in headers.items()}
                auth = headers.get("authorization", "")
                if not secrets.compare_digest(auth, f"Bearer {self.token}"):
                    await self._reply(writer, 401)
                    return
                # Reject browser-origin requests; clients communicate directly over loopback.
                if "origin" in headers or path != "/mcp":
                    await self._reply(writer, 403)
                    return
                if method != "POST":
                    await self._reply(writer, 405)
                    return
                length = int(headers.get("content-length", "0"))
                if length < 1 or length > 1024 * 1024:
                    await self._reply(writer, 413)
                    return
                request = json.loads(await reader.readexactly(length))
                response = await self._dispatch(request)
                await self._reply(writer, 202 if response is None else 200, response)
        except (ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            await self._reply(writer, 400)
        except TimeoutError:
            await self._reply(writer, 408)
        finally:
            writer.close()
            await writer.wait_closed()
            self.connections.discard(task)

    @staticmethod
    async def _reply(writer, status, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode()
        writer.write(
            f"HTTP/1.1 {status} Response\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )
        await writer.drain()

    async def _dispatch(self, request):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            raise ValueError("Invalid JSON-RPC")
        method = request.get("method")
        if not isinstance(request.get("params", {}), dict):
            raise ValueError("Invalid parameters")
        if "id" not in request:
            return None
        response = {"jsonrpc": "2.0", "id": request["id"]}
        if method == "initialize":
            version = request.get("params", {}).get("protocolVersion", "2024-11-05")
            response["result"] = {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "jarvis", "version": "1.0.0"},
            }
        elif method == "ping":
            response["result"] = {}
        elif method == "tools/list":
            response["result"] = {"tools": [
                {"name": tool.name, "description": tool.description,
                 "inputSchema": tool.input_schema} for tool in self.core.tools
            ]}
        elif method == "tools/call":
            params = request.get("params", {})
            name, args = params.get("name"), params.get("arguments", {})
            spec = next((tool for tool in self.core.tools if tool.name == name), None)
            valid = spec is not None and isinstance(args, dict)
            if valid:
                valid = set(args) == set(spec.properties) and all(
                    isinstance(args[key], bool if schema["type"] == "boolean" else str)
                    for key, schema in spec.properties.items()
                )
            if not valid:
                response["error"] = {"code": -32602, "message": "Unknown tool or invalid arguments"}
            else:
                started = time.monotonic()
                log.info("[JOB:jarvis-tool] start tool=%s", name)
                result = await self.core.execute_tool(name, args)
                log.info("[JOB:jarvis-tool] complete tool=%s elapsed_ms=%d", name,
                         (time.monotonic() - started) * 1000)
                response["result"] = {"content": [{"type": "text", "text": result}]}
        else:
            response["error"] = {"code": -32601, "message": "Method not found"}
        return response

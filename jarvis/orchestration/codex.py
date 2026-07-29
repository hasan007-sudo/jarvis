"""Codex app-server adapter for the shared Jarvis orchestrator."""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from pathlib import Path

from ..config import JARVIS_HOME
from .core import OrchestratorCore


class CodexAppServer:
    """Minimal async JSON-RPC client for a local Codex app-server process."""

    def __init__(self, binary: str, owner: OrchestratorCore):
        self.binary = binary
        self.owner = owner
        self.process = None
        self.thread_id: str | None = None
        self._next_id = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._notifications: asyncio.Queue = asyncio.Queue()
        self._write_lock = asyncio.Lock()
        self._reader_task = None
        self._stderr_task = None
        self._stderr_lines: deque[str] = deque(maxlen=8)

    async def start(self) -> str:
        try:
            environment = _app_server_environment()
            self.process = await asyncio.create_subprocess_exec(
                *_app_server_command(self.binary),
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Codex binary not found: {self.binary}") from exc
        self._reader_task = asyncio.create_task(self._read_messages())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self.request("initialize", {
            "clientInfo": {"name": "jarvis", "title": "Jarvis", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        })
        await self.notify("initialized", {})
        options = self.owner.cfg.models.orchestrator.codex
        response = await self.request("thread/start", {
            "model": options.model,
            "cwd": str(JARVIS_HOME),
            "approvalPolicy": "never",
            "sandbox": options.sandbox,
            "ephemeral": options.ephemeral,
            "personality": "friendly",
            "baseInstructions": self.owner.system_prompt,
            "dynamicTools": [
                {
                    "type": "function",
                    "name": spec.name,
                    "description": spec.description,
                    "inputSchema": spec.input_schema,
                    "deferLoading": False,
                }
                for spec in self.owner.tools
            ],
            "environments": [],
            "selectedCapabilityRoots": [],
            "serviceName": "jarvis",
        })
        self.thread_id = response["thread"]["id"]
        return self.thread_id

    async def send(self, text: str) -> str:
        assert self.thread_id is not None
        while not self._notifications.empty():
            self._notifications.get_nowait()
        response = await self.request("turn/start", {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text}],
        })
        turn_id = response["turn"]["id"]
        chunks: list[str] = []
        completed_text = ""
        while True:
            message = await self._notifications.get()
            method = message.get("method", "")
            params = message.get("params") or {}
            if params.get("threadId") != self.thread_id:
                continue
            if method == "item/agentMessage/delta" and params.get("turnId") == turn_id:
                chunks.append(str(params.get("delta", "")))
            elif method == "item/completed" and params.get("turnId") == turn_id:
                item = params.get("item") or {}
                if item.get("type") == "agentMessage":
                    completed_text = str(item.get("text", ""))
            elif method == "turn/completed" and (params.get("turn") or {}).get("id") == turn_id:
                turn = params["turn"]
                if turn.get("status") == "failed":
                    error = turn.get("error") or {}
                    raise RuntimeError(error.get("message") or "Codex turn failed")
                return completed_text.strip() or "".join(chunks).strip()

    async def request(self, method: str, params: dict) -> dict:
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write({"id": request_id, "method": method, "params": params})
        return await future

    async def notify(self, method: str, params: dict) -> None:
        await self._write({"method": method, "params": params})

    async def _write(self, message: dict) -> None:
        if not self.process or not self.process.stdin:
            raise RuntimeError("Codex app-server is not running")
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def _read_messages(self) -> None:
        assert self.process and self.process.stdout
        async for raw in self.process.stdout:
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "id" in message and "method" in message:
                asyncio.create_task(self._handle_server_request(message))
            elif "id" in message:
                future = self._pending.pop(message["id"], None)
                if future and not future.done():
                    if "error" in message:
                        future.set_exception(RuntimeError(str(message["error"])))
                    else:
                        future.set_result(message.get("result") or {})
            elif "method" in message:
                await self._notifications.put(message)
        detail = self._stderr_lines[-1] if self._stderr_lines else "no diagnostic output"
        error = RuntimeError(f"Codex app-server stopped unexpectedly: {detail}")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)

    async def _handle_server_request(self, message: dict) -> None:
        method = message["method"]
        params = message.get("params") or {}
        if method == "item/tool/call":
            result = await self.owner.execute_tool(str(params.get("tool", "")), params.get("arguments") or {})
            payload = {"contentItems": [{"type": "inputText", "text": result}], "success": True}
        elif method == "item/permissions/requestApproval":
            payload = {"scope": "turn", "permissions": {}}
        elif method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            payload = {"decision": "decline"}
        else:
            payload = {"decision": "decline"}
        await self._write({"id": message["id"], "result": payload})

    async def _drain_stderr(self) -> None:
        assert self.process and self.process.stderr
        async for raw in self.process.stderr:
            line = raw.decode(errors="replace").strip()
            if line:
                self._stderr_lines.append(line)
            if "ERROR" in line:
                self.owner.io.notify(f"Codex: {line[-240:]}")

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in (self._reader_task, self._stderr_task):
            if task:
                task.cancel()


class CodexOrchestrator(OrchestratorCore):
    """Run the long-lived Jarvis conversation through Codex app-server."""

    async def run(self) -> None:
        watcher = asyncio.create_task(self.confirm_watcher())
        client = CodexAppServer(self.cfg.codex_bin, self)
        turns = 0
        try:
            thread_id = await client.start()
            self.memory.set("orchestrator_session.codex", thread_id)
            self.io.say(f"Jarvis online via Codex. Default worker: {self.cfg.brain}. What do you need?")
            while True:
                user = (await self.io.ask()).strip()
                if not user:
                    continue
                if user.lower().strip(" .!") in {"exit", "quit", "bye", "goodbye", "shut down"}:
                    self.warn_running_tasks()
                    if turns:
                        summary = await client.send("Summarize this conversation and open threads in two plain sentences.")
                        if summary:
                            self.memory.save_summary(summary)
                    self.io.say("Goodbye.")
                    return
                if self.resolve_confirmation(user):
                    continue
                turns += 1
                reply = await client.send(user)
                if reply:
                    self.io.say(reply)
        finally:
            watcher.cancel()
            await client.close()


def _app_server_command(binary: str) -> list[str]:
    return [
        binary, "app-server", "--stdio",
        "--disable", "apps",
        "--disable", "plugins",
        "--disable", "plugin_sharing",
    ]


def _app_server_environment() -> dict[str, str]:
    """Use a clean Codex home while sharing only the user's login file."""
    source_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    isolated_home = JARVIS_HOME / "codex-home"
    isolated_home.mkdir(parents=True, exist_ok=True)
    source_auth = source_home / "auth.json"
    isolated_auth = isolated_home / "auth.json"
    if source_auth.exists() and not isolated_auth.exists() and source_auth != isolated_auth:
        isolated_auth.symlink_to(source_auth)
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(isolated_home)
    return environment

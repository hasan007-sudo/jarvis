"""Bounded native CLI transport; protocol adapters return assistant text only."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time

log = logging.getLogger(__name__)


class CLIProcess:
    def __init__(self, command, cwd, env):
        self.command, self.cwd, self.env = command, cwd, env
        self.proc = None
        self.drain = None

    async def start(self):
        self.proc = await asyncio.create_subprocess_exec(
            *self.command, cwd=self.cwd, env=self.env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=4 * 1024 * 1024,
            start_new_session=True,
        )
        self.drain = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self):
        # Provider diagnostics can contain prompts, credentials, or reasoning.
        while await self.proc.stderr.read(65536):
            pass

    async def send(self, text, close=False):
        self.proc.stdin.write(text.encode())
        await self.proc.stdin.drain()
        if close:
            self.proc.stdin.close()

    async def event(self):
        line = await self.proc.stdout.readline()
        if not line:
            return None
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("Provider emitted invalid JSON; response withheld") from exc
        if not isinstance(event, dict):
            raise RuntimeError("Provider emitted an invalid event")
        return event

    async def close(self):
        if self.proc is None:
            return
        if self.proc.returncode is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except TimeoutError:
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await self.proc.wait()
        if self.drain:
            self.drain.cancel()
            await asyncio.gather(self.drain, return_exceptions=True)


class OpenCodeSession:
    def __init__(self, binary, settings, cwd, env):
        self.binary, self.settings = binary, settings
        self.cwd, self.env = cwd, env
        self.session_id = None
        self.transport = None

    async def query(self, prompt):
        command = [self.binary, "run", "--pure", "--format", "json", "--agent", "jarvis",
                   "--model", self.settings.model, "--dir", str(self.cwd)]
        if self.settings.variant:
            command.extend(["--variant", self.settings.variant])
        if self.session_id:
            command.extend(["--session", self.session_id])
        self.transport = CLIProcess(command, self.cwd, self.env)
        started = time.monotonic()
        log.info("[LLM:opencode] start model=%s", self.settings.model)
        try:
            async with asyncio.timeout(240):
                await self.transport.start()
                await self.transport.send(prompt, close=True)
                text = []
                complete = False
                while (event := await self.transport.event()) is not None:
                    if event.get("sessionID"):
                        self.session_id = event["sessionID"]
                    if event.get("type") == "error":
                        raise RuntimeError("OpenCode returned an error; check provider authentication/model")
                    if event.get("type") == "text":
                        part = event.get("part", {})
                        if isinstance(part.get("text"), str):
                            text.append(part["text"])
                    if event.get("type") == "step_finish":
                        complete = event.get("part", {}).get("reason") == "stop"
                code = await self.transport.proc.wait()
                if code != 0 or not complete:
                    raise RuntimeError(f"OpenCode did not complete successfully (exit {code})")
                return "\n".join(text).strip()
        except BaseException as exc:
            log.warning("[LLM:opencode] failed error_type=%s", type(exc).__name__)
            raise
        finally:
            await self.transport.close()
            log.info("[LLM:opencode] complete elapsed_ms=%d", (time.monotonic() - started) * 1000)

    async def close(self):
        if self.transport:
            await self.transport.close()

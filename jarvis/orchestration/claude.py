"""Claude Agent SDK adapter for the shared Jarvis orchestrator."""

from __future__ import annotations

import asyncio

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
    PermissionResultAllow, PermissionResultDeny, ResultMessage, TextBlock,
    create_sdk_mcp_server, tool,
)

from ..config import JARVIS_HOME
from .core import OrchestratorCore


class ClaudeOrchestrator(OrchestratorCore):
    """Run Jarvis conversation turns through Claude Agent SDK."""

    def __init__(self, cfg, io):
        super().__init__(cfg, io)
        self._claude_tools = [self._make_tool(spec) for spec in self.tools]
        self._allowed = {f"mcp__jarvis__{spec.name}" for spec in self.tools}

    def _make_tool(self, spec):
        python_schema = {
            name: bool if schema["type"] == "boolean" else str
            for name, schema in spec.properties.items()
        }

        async def handler(args: dict) -> dict:
            result = await self.execute_tool(spec.name, args)
            return {"content": [{"type": "text", "text": result}]}

        return tool(spec.name, spec.description, python_schema)(handler)

    async def _gate(self, tool_name: str, tool_input: dict, context):
        if tool_name in self._allowed:
            return PermissionResultAllow()
        return PermissionResultDeny(message="Jarvis is orchestrator-only; delegate the task.")

    def _options(self, resume: str | None) -> ClaudeAgentOptions:
        server = create_sdk_mcp_server(name="jarvis", tools=self._claude_tools)
        return ClaudeAgentOptions(
            cwd=str(JARVIS_HOME),
            model=self.cfg.models.orchestrator.claude.model,
            system_prompt=self.system_prompt,
            mcp_servers={"jarvis": server},
            allowed_tools=sorted(self._allowed),
            disallowed_tools=["Bash", "Write", "Edit", "NotebookEdit", "Read", "Glob",
                              "Grep", "Task", "Skill", "WebSearch", "WebFetch"],
            setting_sources=[],
            permission_mode="default",
            can_use_tool=self._gate,
            resume=resume or None,
        )

    async def run(self) -> None:
        watcher = asyncio.create_task(self.confirm_watcher())
        key = "orchestrator_session.claude"
        resume = self.memory.get(key) or self.memory.get("orchestrator_session")
        try:
            try:
                async with ClaudeSDKClient(self._options(resume)) as client:
                    await self._loop(client, key)
            except Exception:
                if not resume:
                    raise
                self.memory.set(key, "")
                async with ClaudeSDKClient(self._options(None)) as client:
                    await self._loop(client, key)
        finally:
            watcher.cancel()

    async def _loop(self, client: ClaudeSDKClient, session_key: str) -> None:
        self.io.say(f"Jarvis online via Claude. Default worker: {self.cfg.brain}. What do you need?")
        turns = 0
        while True:
            user = (await self.io.ask()).strip()
            if not user:
                continue
            if user.lower().strip(" .!") in {"exit", "quit", "bye", "goodbye", "shut down"}:
                await self._wrap_up(client, turns)
                return
            if self.resolve_confirmation(user):
                continue
            turns += 1
            await client.query(user)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            self.io.say(block.text.strip())
                elif isinstance(message, ResultMessage) and message.session_id:
                    self.memory.set(session_key, message.session_id)

    async def _wrap_up(self, client: ClaudeSDKClient, turns: int) -> None:
        self.warn_running_tasks()
        if turns:
            await client.query("Summarize this conversation and open threads in two plain sentences.")
            summary = ""
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            summary = block.text.strip()
            if summary:
                self.memory.save_summary(summary)
        self.io.say("Goodbye.")


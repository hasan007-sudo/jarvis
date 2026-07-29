"""Claude worker: one Agent SDK session per task, in the project directory.

setting_sources=["user", "project"] loads the user's real Claude Code setup
(CLAUDE.md, skills, custom agents, hooks, MCP servers) so a delegated task
behaves exactly like typing it into Claude Code. Every tool call routes
through the Jarvis policy engine via can_use_tool.
"""

from __future__ import annotations

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
)

from ..config import Config
from ..policy import Decision, PolicyEngine
from . import TaskResult

WORKER_APPEND = """
You are a worker agent spawned by Jarvis, a voice assistant, to complete one
delegated task. Work autonomously. Your final message is read ALOUD to the
user, so end with a 2-4 sentence plain-language summary of what you did and
how you verified it — no file-by-file lists, no markdown tables.
"""


def _describe(tool_name: str, tool_input: dict, reason: str) -> str:
    if tool_name == "Bash":
        return f"run `{str(tool_input.get('command', ''))[:150]}`"
    if reason:
        return reason
    return f"use {tool_name}"


class ClaudeWorker:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    async def run(self, task, on_event, gate) -> TaskResult:
        policy = PolicyEngine(project=task.project, auto_edits=task.auto_edits)

        async def can_use(tool_name: str, tool_input: dict, context):
            verdict = policy.evaluate(tool_name, tool_input)
            if verdict.decision is Decision.ALLOW:
                return PermissionResultAllow()
            if verdict.decision is Decision.DENY:
                on_event("denied", f"{tool_name}: {verdict.reason}")
                return PermissionResultDeny(
                    message=f"Blocked by Jarvis policy: {verdict.reason}"
                )
            approved = await gate(_describe(tool_name, tool_input, verdict.reason))
            if approved:
                return PermissionResultAllow()
            return PermissionResultDeny(
                message="The user declined this action. Adjust your approach or stop."
            )

        options = ClaudeAgentOptions(
            cwd=str(task.project),
            model=self.cfg.models.brain.claude.model,
            setting_sources=["user", "project"],
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": WORKER_APPEND,
            },
            permission_mode="default",
            can_use_tool=can_use,
        )

        last_text = ""
        async with ClaudeSDKClient(options) as client:
            await client.query(task.instructions)
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock) and block.text.strip():
                            last_text = block.text.strip()
                            on_event("text", last_text)
                        elif isinstance(block, ToolUseBlock):
                            on_event("tool", block.name)
                elif isinstance(msg, ResultMessage):
                    summary = (msg.result or last_text or "Finished.").strip()
                    return TaskResult(
                        ok=not msg.is_error,
                        summary=summary,
                        session_id=msg.session_id,
                    )
        return TaskResult(ok=False, summary="Worker ended without a result.")

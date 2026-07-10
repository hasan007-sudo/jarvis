"""Permission policy for worker agents.

Three tiers:
- ALLOW   read-only tools, edits inside the project when the task was granted
          auto-edits, and writes to Jarvis's own memory dir.
- CONFIRM anything that changes state and wasn't pre-approved: edits outside
          the project, app control, pushes/publishes, unknown MCP tools.
- DENY    privilege escalation and destructive/system-level operations.
          Enforced in code regardless of what the model asks for.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .config import JARVIS_HOME


class Decision(Enum):
    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"


@dataclass
class Verdict:
    decision: Decision
    reason: str = ""


READ_ONLY_TOOLS = {
    "Read",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "TodoWrite",
    "NotebookRead",
    "Task",
    "Skill",
    "BashOutput",
    "ListMcpResourcesTool",
    "ReadMcpResourceTool",
}

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

DENY_BASH = [
    (r"\bsudo\b", "privilege escalation (sudo) is never allowed"),
    (r"\brm\s+(-\w*[rf]\w*\s+)+(/|~|\$HOME)(\s|$)", "recursive delete of home or root"),
    (r"\bsecurity\s+", "keychain access is not allowed"),
    (r"\b(shutdown|reboot|halt)\b", "system power commands are not allowed"),
    (r"\bdiskutil\s+(erase|partition|reformat)", "disk destruction is not allowed"),
    (r"\bmkfs\b", "disk formatting is not allowed"),
    (r"\bcsrutil\b", "SIP changes are not allowed"),
    (r":\(\)\s*\{.*\};:", "fork bomb"),
]

CONFIRM_ALWAYS_BASH = [
    (r"\bgit\s+push\b", "pushes to a remote"),
    (r"\b(publish|deploy)\b", "publishes/deploys"),
    (r"\bgh\s+(pr|release|repo)\s+(create|merge|delete)", "acts on GitHub"),
]

READ_ONLY_BASH_PREFIXES = (
    "ls", "pwd", "cat", "head", "tail", "wc", "which", "file", "stat",
    "du", "df", "rg", "grep", "find", "echo", "printenv", "env", "uname",
    "sw_vers", "date", "whoami", "tree",
)

READ_ONLY_GIT = ("status", "log", "diff", "show", "branch", "remote", "blame", "shortlog")

# MCP tools whose names look read-only get a pass; the rest confirm.
MCP_READ_PREFIXES = ("get_", "list_", "read_", "search_", "query_", "resolve_", "fetch_", "find_")


class PolicyEngine:
    def __init__(self, project: Path | None, auto_edits: bool):
        self.project = project.resolve() if project else None
        self.auto_edits = auto_edits

    def evaluate(self, tool_name: str, tool_input: dict) -> Verdict:
        if tool_name in READ_ONLY_TOOLS:
            return Verdict(Decision.ALLOW)

        if tool_name in EDIT_TOOLS:
            return self._evaluate_edit(tool_input)

        if tool_name == "Bash":
            return self._evaluate_bash(str(tool_input.get("command", "")))

        if tool_name.startswith("mcp__"):
            short = tool_name.split("__")[-1]
            if short.startswith(MCP_READ_PREFIXES):
                return Verdict(Decision.ALLOW)
            return Verdict(Decision.CONFIRM, f"MCP action '{short}'")

        return Verdict(Decision.CONFIRM, f"unrecognized tool '{tool_name}'")

    def _evaluate_edit(self, tool_input: dict) -> Verdict:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        path = Path(raw)
        if not path.is_absolute() and self.project:
            path = self.project / path
        try:
            path = path.resolve()
        except OSError:
            return Verdict(Decision.CONFIRM, f"edit to unresolvable path {raw!r}")

        if path.is_relative_to(JARVIS_HOME / "memory"):
            return Verdict(Decision.ALLOW)
        if self.project and path.is_relative_to(self.project):
            if self.auto_edits:
                return Verdict(Decision.ALLOW)
            return Verdict(Decision.CONFIRM, f"edit inside project: {path.name}")
        return Verdict(Decision.CONFIRM, f"edit OUTSIDE the project: {path}")

    def _evaluate_bash(self, command: str) -> Verdict:
        for pattern, reason in DENY_BASH:
            if re.search(pattern, command):
                return Verdict(Decision.DENY, reason)
        for pattern, reason in CONFIRM_ALWAYS_BASH:
            if re.search(pattern, command):
                return Verdict(Decision.CONFIRM, reason)

        if self._is_read_only_bash(command):
            return Verdict(Decision.ALLOW)
        if self.auto_edits:
            return Verdict(Decision.ALLOW)
        return Verdict(Decision.CONFIRM, f"shell command: {command[:120]}")

    def _is_read_only_bash(self, command: str) -> bool:
        # Every segment of a compound command must be read-only.
        segments = re.split(r"&&|\|\||;|\|", command)
        for segment in segments:
            try:
                words = shlex.split(segment.strip())
            except ValueError:
                return False
            if not words:
                continue
            if words[0] == "git":
                if len(words) < 2 or words[1] not in READ_ONLY_GIT:
                    return False
            elif words[0] == "cd":
                continue
            elif words[0] not in READ_ONLY_BASH_PREFIXES:
                return False
            # Redirection writes a file — not read-only.
            if ">" in segment:
                return False
        return True

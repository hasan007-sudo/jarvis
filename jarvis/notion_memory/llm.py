"""One-shot Claude/Codex call reusing jarvis's cheap read-only `sync` model role."""
from __future__ import annotations

import subprocess


def run_llm(cfg, prompt: str, stdin: str) -> str:
    role = cfg.models.sync
    if role.provider == "claude":
        cmd = ["claude", "-p", "--model", role.claude.model, prompt]
    else:
        c = role.codex
        cmd = [cfg.codex_bin, "exec", "--model", c.model,
               "--sandbox", "read-only", "--skip-git-repo-check"]
        if c.ephemeral:
            cmd.append("--ephemeral")
        cmd.append(prompt)
    try:
        out = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=240)
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return f"[notes ask] LLM call failed: {exc}"
    return out.stdout.strip() or "[notes ask] empty response."

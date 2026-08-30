"""Provider-neutral Jarvis conversation orchestration."""

from __future__ import annotations

from ..config import Config


def create_orchestrator(cfg: Config, io):
    provider = cfg.models.orchestrator.provider
    if provider in ("opencode", "antigravity"):
        from .external import ExternalOrchestrator

        return ExternalOrchestrator(cfg, io)
    if provider == "codex":
        from .codex import CodexOrchestrator

        return CodexOrchestrator(cfg, io)
    if provider == "claude":
        try:
            from .claude import ClaudeOrchestrator
        except ImportError as exc:
            raise RuntimeError(
                "Claude orchestration requires the optional Claude support. "
                "Reinstall Jarvis with the 'claude' extra."
            ) from exc
        return ClaudeOrchestrator(cfg, io)
    raise ValueError(f"Unknown orchestrator provider: {provider!r}")

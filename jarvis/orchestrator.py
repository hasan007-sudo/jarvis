"""Backward-compatible import for the provider-neutral orchestrator factory."""

from .orchestration import create_orchestrator


def Orchestrator(cfg, io):
    return create_orchestrator(cfg, io)

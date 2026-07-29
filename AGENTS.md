# Jarvis agent guide

Jarvis is a macOS voice orchestrator with a shared core and native Claude and
Codex conversation adapters. It delegates implementation work to separate
Claude or Codex workers. `jarvis sync` distills local session history into the
configured second-brain vault.

## Configuration boundaries

- `~/.jarvis/config.yaml`: application, voice, project, binary, and vault
  settings.
- `~/.jarvis/models.yaml`: AI provider, model, sandbox, and session-persistence
  settings.
- `orchestrator` in `models.yaml`: main conversation provider.
- `brain` in `models.yaml`: default delegated worker.
- `sync` in `models.yaml`: independent provider used only to distill history.

Read `docs/MODEL_CONFIGURATION.md` before changing provider behavior.

## Runtime call paths

```text
jarvis chat/talk/daemon
  → create_orchestrator()
  → ClaudeOrchestrator | CodexOrchestrator
  → spawn_task()
  → get_worker(cfg.brain)
  → ClaudeWorker.run() | CodexWorker.run()

jarvis sync
  → SecondBrain.sync()
  → parse_claude() | parse_codex()
  → SecondBrain.distill()
  → claude -p | codex exec       # selected by models.sync.provider
```

## Development setup

```sh
uv sync
uv run jarvis --help
```

Use `uv`, which matches `uv.lock`. Do not add dependencies for model selection;
the application reuses authenticated Claude Code and Codex CLI installations.
Claude Agent SDK is an optional dependency and must stay lazily imported so a
Codex-only installation remains functional.

# Jarvis agent guide

Jarvis is a macOS voice orchestrator with a shared core and native Claude,
Codex, and OpenCode conversation adapters. It delegates implementation work to separate
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
For installation on another machine or continuing provider work, also read
`docs/PROVIDER_SETUP.md`: it records prerequisites, verification steps, dated
findings, and unresolved AGY integration checks.

## Provider invariants

- Preserve dirty work and verify the actual installed executable/checkout.
- Keep conversation, worker, and sync selections independent. OpenCode/AGY
  worker support and history importers are not implemented.
- Reuse the shared core, task manager, tool schemas, and approval flow.
  Native MCP/tool calls must not become text-encoded pretend tool calls.
- External-provider sync runs without tools. Speak assistant text only, never
  raw diagnostics, reasoning, credentials, or protocol events.
- AGY execution remains disabled until discovery, actual native-tool
  restrictions, and a positive permitted MCP round trip are verified.
  `init.agent`, `init.tools`, or `status: SUCCESS` alone cannot prove this.
- Do not change global provider permissions, copy auth tokens, create persistent
  provider projects, or enable paid balance usage without user authorization.
- Saved model settings, a running daemon, and a working model request are
  distinct states. Verify all three before claiming a successful switch.

## Runtime call paths

```text
jarvis chat/talk/daemon
  → create_orchestrator()
  → ClaudeOrchestrator | CodexOrchestrator | ExternalOrchestrator (OpenCode)
  → spawn_task()
  → get_worker(cfg.brain)
  → ClaudeWorker.run() | CodexWorker.run()

jarvis sync
  → SecondBrain.sync()
  → parse_claude() | parse_codex()
  → SecondBrain.distill()
  → claude -p | codex exec | run_sync()  # selected by models.sync.provider

notion_memory.llm.run_llm()
  → same independently configured sync provider
```

## Development setup

```sh
uv sync
uv run jarvis --help
```

Use `uv`, which matches `uv.lock`. Do not add dependencies for model selection;
the application reuses authenticated native provider CLI installations.
Claude Agent SDK is an optional dependency and must stay lazily imported so a
Codex-only installation remains functional.

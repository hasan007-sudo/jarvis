# Model configuration

For cross-machine installation, native model/tool checks, restart verification,
and the dated AGY handoff, read [Provider setup](PROVIDER_SETUP.md).

Jarvis configures AI providers independently for three roles:

```text
User ↔ Jarvis orchestrator (Claude, Codex, or OpenCode native adapter)
              ├─ delegated task → brain provider (Claude or Codex)
              └─ history distillation → sync provider (Claude, Codex, or OpenCode)
```

The orchestrator, delegated workers, and history sync are independently
configurable in `~/.jarvis/models.yaml`. Each conversation provider uses its own
native adapter; Codex and OpenCode are not run inside Claude Agent SDK.
Antigravity configuration keys are reserved, but execution is unavailable until
native CLI tool isolation is verified. The CLI refuses to switch to Antigravity.

## Quick setup

Install and authenticate at least the provider selected for each role:

```sh
claude login
codex login
```

Jarvis creates `~/.jarvis/models.yaml` on first run. The default configuration
uses Codex GPT-5.5 for both delegated work and history sync:

```yaml
orchestrator:
  provider: codex
  claude:
    model: sonnet
  codex:
    model: gpt-5.5
    sandbox: read-only
    ephemeral: false
    skip_git_repo_check: true
    ignore_user_config: false
    ignore_rules: false

brain:
  provider: codex
  claude:
    model: sonnet
  codex:
    model: gpt-5.5
    sandbox: workspace-write
    ephemeral: false
    skip_git_repo_check: true
    ignore_user_config: false
    ignore_rules: false

sync:
  provider: codex
  claude:
    model: haiku
  codex:
    model: gpt-5.5
    sandbox: read-only
    ephemeral: true
    skip_git_repo_check: true
    ignore_user_config: true
    ignore_rules: true
```

Model availability depends on the user's Claude or Codex account. Replace a
model name with one available to that account if the provider rejects it.

## Switching providers

To switch the main conversation to Codex:

```sh
jarvis orchestrator codex
jarvis install-daemon
```

The daemon must restart because it keeps one long-lived provider session.

To make Codex the default delegated worker:

```yaml
brain:
  provider: codex
```

The equivalent CLI command is `jarvis brain codex`. It updates
`brain.provider` in `models.yaml`.

To use Claude for conversation-history sync:

```yaml
sync:
  provider: claude
```

Provider choices are independent. Switching a provider does not erase either
provider's saved model settings.

For a one-subscription setup, switch all three roles together:

```sh
jarvis provider codex   # or: jarvis provider claude
```

## One-subscription installations

Codex-only users install the base package plus voice support. Claude Agent SDK
is not imported or required:

```sh
uv tool install "jarvis-assistant[voice] @ git+https://github.com/hasan007-sudo/jarvis"
```

Claude users add the optional Claude adapter:

```sh
uv tool install "jarvis-assistant[voice,claude] @ git+https://github.com/hasan007-sudo/jarvis"
```

The installer detects the available CLI. On a fresh installation it configures
all three roles for Codex when Codex is installed, otherwise for Claude. It
does not overwrite an existing `models.yaml` during upgrades.

## Codex options

- `model`: passed to `codex exec --model`.
- `sandbox`: `read-only`, `workspace-write`, or `danger-full-access`.
- `ephemeral`: adds `--ephemeral`, preventing the run from being saved as a
  new Codex conversation. Keep this enabled for sync to avoid resyncing sync
  sessions.
- `skip_git_repo_check`: adds `--skip-git-repo-check`. This allows registered
  projects or second-brain vaults that are not Git repositories.
- `ignore_user_config`: adds `--ignore-user-config`. Sync enables this so a
  user's plugins, MCP servers, and other Codex customizations cannot interfere
  with a non-interactive distillation run. Codex authentication is preserved.
- `ignore_rules`: adds `--ignore-rules`. Sync does not need repository agent
  instructions because its prompt has one narrowly defined output format.

The safe defaults deliberately differ: delegated workers receive
`workspace-write` and the user's normal Codex setup, while sync is read-only,
ephemeral, and isolated from unrelated customizations.

## Claude options

`model` is passed to Claude Agent SDK for delegated workers and to
`claude -p --model` for sync. Aliases such as `sonnet` and `haiku` may resolve
to newer versions over time. Use a full model identifier when reproducibility
is more important than automatically following the latest alias.

## Codex orchestrator boundary

The Codex adapter uses the official local `codex app-server --stdio` protocol:
one process, one conversation thread, streamed turns, and client-handled Jarvis
tools. The orchestrator starts with no coding environment attached, read-only
sandboxing, no approval escalation, and an empty capability-root selection. It
uses `~/.jarvis/codex-home` for clean runtime configuration and symlinks only
the existing Codex `auth.json`; user MCP servers and plugins are not loaded.
Codex dynamic tools are currently an experimental app-server capability, so
all protocol handling is isolated in `jarvis/orchestration/codex.py`.

## OpenCode and Antigravity

OpenCode is available for conversations and independent history distillation.
Delegated workers remain Claude or Codex: `jarvis brain` and `jarvis provider`
reject other providers, because those commands would change the worker role.
Existing Claude/Codex settings and defaults are unchanged.

Set an explicit model before selecting either new provider. Model identifiers
are passed through unchanged; Jarvis does not maintain a model catalog or guess
an account's default model. For example, replace the placeholders below with
models available in your authenticated OpenCode installation:

```yaml
orchestrator:
  provider: opencode
  opencode:
    model: YOUR_PROVIDER/YOUR_MODEL
    variant: ""  # optional provider-specific variant
sync:
  provider: opencode
  opencode:
    model: YOUR_PROVIDER/YOUR_MODEL
```

Then use `jarvis orchestrator opencode`; restart a running daemon yourself to
apply changes. `opencode_bin` and `agy_bin` belong in `config.yaml`, and may be
absolute executable paths. Authenticate with the provider's normal CLI first.
Jarvis never changes provider credentials or the user's global provider config.

OpenCode uses the v1 `run --format json` protocol (developed against 1.18.25).
OpenCode v2 is not validated. Its native MCP client connects to one authenticated
loopback HTTP server inside Jarvis, which calls the same shared tools and task
manager as the existing adapters. All other tools are denied. Each conversation
keeps an explicit OpenCode session ID across turns; a new Jarvis process starts a
fresh conversation. Only assistant text reaches speech output. Processes time out
after 240 seconds and are terminated on cancellation; raw diagnostics, reasoning,
tool events, and credential-bearing config are never spoken or logged by Jarvis.

Each runtime uses a temporary workspace and isolated OpenCode user configuration,
with external plugins disabled. Normal provider data/authentication remains in
place. Custom provider definitions and authentication plugins from user config
are not imported. Organization-managed configuration may take precedence over
runtime settings; such managed installations require separate policy verification.
Sync runs without MCP tools and without resuming any conversation. OpenCode may
retain its own native session record; Jarvis currently imports only Claude/Codex
history, so these runs are not reimported by `jarvis sync`.

Antigravity accepts `provider: antigravity` and an explicit
`antigravity.model`, but execution currently fails closed with a clear error.
On CLI 1.1.22, explicitly registering the disposable project fixed custom-agent
discovery. The default CLI project had silently fallen back to its default
agent. Initialization still echoes the requested name and lists a broad tool
registry, so neither field proves effective permissions.

After the user added a project-scoped grant on 2026-08-31, the discovered agent
completed a native MCP stub call and retained its result across a resumed turn.
A disposable native write remained unavailable; a separate tool-free JSON reply
also passed. These probes do not verify the complete production adapter. A denied
MCP call previously produced a final `SUCCESS` with empty text. See
[the AGY enablement gate](PROVIDER_SETUP.md#agy-investigation-and-enablement-gate)
for evidence and the remaining checks. Jarvis does not send AGY user prompts
or transcripts until the integration is verified. `--sandbox` alone is not a
read-only tool boundary; do not rewrite global permissions to work around this.

Protocol references: [OpenCode config](https://opencode.ai/docs/config/),
[permissions](https://opencode.ai/docs/permissions/),
[MCP](https://opencode.ai/docs/mcp-servers/), and
[Antigravity headless](https://antigravity.google/docs/cli/headless/).

## Other application configuration

`~/.jarvis/config.yaml` continues to hold non-model settings, including
`codex_bin`, voice configuration, projects, and the second-brain vault.

Existing installations with `brain` in `config.yaml` are migrated safely:
when `models.yaml` is first created, that value seeds `brain.provider`. Future
`jarvis brain` commands update `models.yaml`.

## Troubleshooting

- `command not found`: install and authenticate the selected provider CLI.
- `model not found` or access errors: choose a model enabled for that account.
- invalid provider or sandbox: Jarvis reports the invalid key and the path to
  `models.yaml` during startup.
- sync creates new Codex sessions: ensure `sync.codex.ephemeral` is `true`.

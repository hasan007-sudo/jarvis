# Jarvis

An open-source, always-on voice orchestrator for macOS — in the spirit of Iron
Man's JARVIS. Press a hotkey from any app, speak, and Jarvis delegates real
work to worker agents through Claude Code and/or Codex CLI. Conversations can
also use OpenCode. Authentication, quotas, and billing follow the selected
provider and account; the voice pipeline runs locally.

```
mic ─▶ hotkey (⌃⌥J) ─▶ VAD + whisper.cpp (local STT)
        └▶ Jarvis (Claude SDK, Codex app-server, or OpenCode orchestrator)
             ├── spawn_task ─▶ worker agents per task (claude | codex), concurrent
             │                  └── policy: allow / voice-confirm / hard-deny
             ├── Jarvis control tools · persistent memory · session context
             └── second brain: any folder of notes, searched progressively
speaker ◀── macOS `say` (TTS)          dashboard ◀── http://127.0.0.1:8787
```

## Highlights

- **Provider-independent orchestration** — native Claude Agent SDK, Codex
  app-server, or OpenCode CLI adapters.
  Every implementation task is delegated to a separate worker agent.
- **Your existing AI setup, reused** — Claude workers load your real
  `~/.claude` skills, agents, CLAUDE.md, and MCP servers; Codex workers run
  `codex exec` in its sandbox with your `~/.codex` config. Switch platforms by
  voice: *"switch to codex"*.
- **Safety tiers** — read-only actions auto-run; edits, pushes, and app
  control ask you by voice; sudo/keychain/destructive commands are hard-denied
  in code.
- **A second brain from your history** — `jarvis sync` distills every past
  Claude/Codex session transcript into an Obsidian-friendly markdown vault
  (decisions, problems→solutions, questions, per-project rollups), refreshed
  nightly. Jarvis queries it to answer "what did we decide about X?". Any
  folder can be attached as the second brain via config, whatever its format.
- **Local, free voice** — whisper.cpp (small→large models) or NVIDIA Parakeet
  (MLX) for STT, Silero VAD for end-of-turn (2s of silence, tunable), macOS
  `say` for TTS. Audio never leaves your machine; only transcribed text goes
  to your AI provider.
- **Wispr-Flow-style UX** — global hotkey, chime feedback for
  listening/thinking states, barge-in, hands-free follow-up window, and a live
  web dashboard for state + multi-project task progress.

## Install

Prerequisites: macOS + Homebrew, and at least one of
[Claude Code](https://claude.com/claude-code) (`claude login`) or
[Codex CLI](https://github.com/openai/codex) (`codex login`).

```sh
curl -fsSL https://raw.githubusercontent.com/hasan007-sudo/jarvis/main/install.sh | bash
```

Or manually, for Codex-only use:
`uv tool install "jarvis-assistant[voice] @ git+https://github.com/hasan007-sudo/jarvis"`.
Add the `claude` extra for Claude support: `[voice,claude]`. Then run
`jarvis setup-voice`.

Setting up another Mac or handing work to another coding agent? Start with
[Provider setup and handoff](docs/PROVIDER_SETUP.md). It covers installation
provenance, authentication, exact model IDs, quota checks, daemon restarts, and
the current AGY integration gate. AGY execution is not enabled yet.

## Usage

```sh
jarvis chat             # text mode
jarvis talk             # push-to-talk voice (terminal)
jarvis daemon           # always-on hotkey voice (foreground)
jarvis install-daemon   # daemon via launchd — starts at login, auto-restarts
jarvis stop             # stop the background daemon and dashboard
jarvis ui               # live dashboard (state, tasks, approvals, transcript)
jarvis sync [--max N]   # distill past sessions into the second brain
jarvis install-sync     # nightly sync at 21:30
jarvis brain codex      # default worker platform
jarvis orchestrator codex # main conversation provider (restart daemon after changing)
jarvis provider codex   # set orchestrator, worker, and sync together
jarvis project add api ~/code/api
jarvis status           # task history
```

**Talking to it:** press **⌃⌥J** → chime → speak → pause 2s → it acknowledges,
thinks, answers aloud → the mic reopens ~6s for follow-ups. Press the hotkey
mid-speech to interrupt. Say *"in project X, implement … — auto-approve
edits"* to delegate; *"status?"* for progress; answer approval requests with
yes/no.

One-time macOS grants for the daemon: Microphone + Input
Monitoring/Accessibility for the resolved python binary
(`readlink -f ~/.local/share/uv/tools/jarvis-assistant/bin/python3`).

## Configuration

Jarvis keeps application settings and AI model settings separate:

- `~/.jarvis/config.yaml` — voice, projects, second-brain vault, and binaries
- `~/.jarvis/models.yaml` — independent conversation, worker, and sync providers

Change only the relevant `provider` line; each provider keeps its own model
settings. Conversations and sync support OpenCode as well as Claude/Codex.
Workers remain Claude/Codex. Configure an explicit OpenCode model before
selecting it; see [Provider setup](docs/PROVIDER_SETUP.md).

```yaml
# ~/.jarvis/models.yaml
orchestrator:
  provider: codex             # claude | codex
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
  provider: codex             # claude | codex
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
  provider: codex             # independent of brain.provider
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

`orchestrator` selects the main conversation provider, `brain` selects the
default delegated worker, and `sync` selects the history-distillation model.
All three are independent. See [Model configuration](docs/MODEL_CONFIGURATION.md).

### Application settings — `~/.jarvis/config.yaml`

```yaml
user_name: Tony             # how Jarvis addresses you
second_brain:
  vault: ~/notes            # ANY folder — Jarvis searches it progressively
voice:
  hotkey: <ctrl>+<alt>+j
  silence_seconds: 2.0      # end-of-turn pause
  stt_engine: whisper       # whisper | parakeet
  whisper_model: ~/.jarvis/models/ggml-large-v3-turbo-q5_0.bin
  tts_voice: Samantha
projects:                   # optional pinned project names
  api: ~/code/api
```

## License

MIT

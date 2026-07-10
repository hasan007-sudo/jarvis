# Jarvis

A voice-driven personal orchestrator for macOS. Jarvis itself never touches
files or shells — it only converses, looks things up, and **delegates every
task to worker agents** (Claude Code via the Agent SDK, or Codex CLI), which
run concurrently in the background using your existing subscriptions, skills,
agents, and MCP servers.

## Architecture

```
mic/keyboard ─▶ Jarvis (orchestrator, delegate-only Claude session)
                  ├── spawn_task ─▶ worker agent per task (claude | codex)
                  │                  └── policy engine: allow / voice-confirm / deny
                  ├── WebSearch / WebFetch (lookups)
                  └── memory: ~/.jarvis/memory + ~/.jarvis/jarvis.db (survives restarts)
speaker ◀── macOS `say`
```

- **Orchestrator-only:** enforced in code via a `can_use_tool` allowlist — the
  Jarvis session has no Bash/Read/Write tools at all.
- **Workers:** Claude workers load your real `~/.claude` setup
  (`setting_sources=["user","project"]`) so skills, custom agents, CLAUDE.md
  and MCP servers all apply. Codex workers run `codex exec --json` inside the
  workspace-write sandbox.
- **Permissions:** read-only actions auto-allow; edits/app-control/pushes ask
  you by voice; sudo, keychain, and destructive commands are hard-denied.
- **Projects:** say a project name; Jarvis searches your home folder
  (`~/Github`, `~/Projects`, ...). Not found or ambiguous → it asks you.

## Usage

Installed globally via `uv tool install -e . --with sounddevice --with numpy --with pynput`.

```sh
jarvis chat             # text mode
jarvis talk             # push-to-talk voice mode (terminal)
jarvis daemon           # always-on hotkey voice mode (foreground)
jarvis install-daemon   # install daemon via launchd (starts at login)
jarvis sync [--max N]   # distill Claude/Codex sessions into the second brain
jarvis install-sync     # daily 21:30 second-brain refresh (launchd)
jarvis status           # task history
jarvis brain codex      # set default worker platform
jarvis project add api ~/Github/Personal/api
```

**Daemon hotkey: Ctrl+Option+J** from any app — chime, speak, pause 2s, Jarvis
answers. Press again while it's talking to barge in. Configure via
`voice.hotkey` / `voice.silence_seconds` in `~/.jarvis/config.yaml`.
One-time macOS grants: Microphone + Input Monitoring/Accessibility for the
Jarvis python binary.

Config: `~/.jarvis/config.yaml` · memory: `~/.jarvis/memory/` ·
second brain vault: `~/Github/Personal/second-brain` (Obsidian + git).

## Roadmap

- Wake word ("hey Jarvis") as an alternative to the hotkey
- Silero VAD (better end-of-turn in noisy rooms); pipecat Smart Turn for semantic end-of-turn
- Kokoro local TTS upgrade

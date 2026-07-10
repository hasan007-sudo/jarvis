# Jarvis

An open-source, always-on voice orchestrator for macOS — in the spirit of Iron
Man's JARVIS. Press a hotkey from any app, speak, and Jarvis delegates real
work to **worker agents running on the AI subscriptions you already have**
(Claude Code and/or Codex CLI). No API keys, no per-token billing, fully local
voice pipeline.

```
mic ─▶ hotkey (⌃⌥J) ─▶ VAD + whisper.cpp (local STT)
        └▶ Jarvis (orchestrator, delegate-only — no file/shell access)
             ├── spawn_task ─▶ worker agents per task (claude | codex), concurrent
             │                  └── policy: allow / voice-confirm / hard-deny
             ├── WebSearch lookups · persistent memory · session resume
             └── second brain: any folder of notes, searched progressively
speaker ◀── macOS `say` (TTS)          dashboard ◀── http://127.0.0.1:8787
```

## Highlights

- **Orchestrator-only by construction** — the Jarvis session has zero file or
  shell tools (enforced via a permission callback, not just prompting). Every
  task spawns a separate worker agent; ask for three features in three repos
  and they run concurrently.
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

Or manually: `uv tool install "jarvis-assistant[voice] @ git+https://github.com/hasan007-sudo/jarvis"`
then `jarvis setup-voice`.

## Usage

```sh
jarvis chat             # text mode
jarvis talk             # push-to-talk voice (terminal)
jarvis daemon           # always-on hotkey voice (foreground)
jarvis install-daemon   # daemon via launchd — starts at login, auto-restarts
jarvis ui               # live dashboard (state, tasks, approvals, transcript)
jarvis sync [--max N]   # distill past sessions into the second brain
jarvis install-sync     # nightly sync at 21:30
jarvis brain codex      # default worker platform
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

## Configuration — `~/.jarvis/config.yaml`

```yaml
user_name: Tony             # how Jarvis addresses you
brain: claude               # default worker: claude | codex
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

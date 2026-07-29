#!/usr/bin/env bash
# Jarvis quick installer (macOS, Apple Silicon recommended)
# Usage: curl -fsSL https://raw.githubusercontent.com/hasan007-sudo/jarvis/main/install.sh | bash
set -euo pipefail

echo "== Jarvis installer =="

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Jarvis currently supports macOS only." >&2
  exit 1
fi

if ! command -v brew >/dev/null; then
  echo "Homebrew is required (https://brew.sh) — install it first." >&2
  exit 1
fi

if ! command -v claude >/dev/null && ! command -v codex >/dev/null; then
  echo "Install at least one brain first:" >&2
  echo "  Claude Code: npm i -g @anthropic-ai/claude-code && claude login" >&2
  echo "  Codex CLI:   npm i -g @openai/codex && codex login" >&2
  exit 1
fi

if ! command -v uv >/dev/null; then
  echo "-- installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "-- installing jarvis..."
EXTRAS="voice"
if command -v claude >/dev/null; then
  EXTRAS="voice,claude"
fi
uv tool install --force "jarvis-assistant[$EXTRAS] @ git+https://github.com/hasan007-sudo/jarvis"

if [[ ! -f "$HOME/.jarvis/models.yaml" ]]; then
  if command -v codex >/dev/null; then
    "$HOME/.local/bin/jarvis" provider codex
  else
    "$HOME/.local/bin/jarvis" provider claude
  fi
fi

echo "-- setting up voice (whisper.cpp + models)..."
"$HOME/.local/bin/jarvis" setup-voice

cat <<'EOF'

== Jarvis installed ==

Try it:
  jarvis chat             # text conversation
  jarvis talk             # push-to-talk voice
  jarvis install-daemon   # always-on hotkey daemon (Ctrl+Option+J), starts at login
  jarvis install-sync     # nightly second-brain sync from Claude/Codex sessions
  jarvis ui               # live status dashboard

First daemon run: grant Microphone + Input Monitoring/Accessibility when
macOS prompts (System Settings > Privacy & Security).
Config: ~/.jarvis/config.yaml (app) and ~/.jarvis/models.yaml (AI providers/models).
EOF

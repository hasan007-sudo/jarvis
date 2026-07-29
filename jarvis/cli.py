"""Jarvis command line: chat (text), talk (voice), status, setup, config."""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from .config import CONFIG_PATH, JARVIS_HOME, Config

WHISPER_MODEL_URL = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.en.bin"
)
VAD_MODEL_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
)


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvis", description="Jarvis voice orchestrator")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("chat", help="text conversation (default)")
    sub.add_parser("talk", help="voice conversation (push-to-talk)")
    sub.add_parser("status", help="recent task history")
    sub.add_parser("setup-voice", help="install whisper.cpp and download the STT model")
    sync = sub.add_parser("sync", help="distill new Claude/Codex sessions into the second brain")
    sync.add_argument("--max", type=int, default=None, help="max sessions this run")
    sub.add_parser("install-sync", help="install the daily second-brain sync (launchd)")
    sub.add_parser("daemon", help="run the always-on hotkey voice daemon (foreground)")
    sub.add_parser("install-daemon", help="install the daemon as a launchd service (starts at login)")
    sub.add_parser("stop", help="stop the background daemon and dashboard")
    sub.add_parser("ui", help="open the Jarvis status dashboard in the browser")
    brain = sub.add_parser("brain", help="show or set the default worker platform")
    brain.add_argument("name", nargs="?", choices=["claude", "codex"])
    orchestrator = sub.add_parser("orchestrator", help="show or set the conversation provider")
    orchestrator.add_argument("name", nargs="?", choices=["claude", "codex"])
    provider = sub.add_parser("provider", help="set orchestrator, worker, and sync together")
    provider.add_argument("name", choices=["claude", "codex"])
    project = sub.add_parser("project", help="list or register projects")
    project.add_argument("action", nargs="?", default="list", choices=["list", "add"])
    project.add_argument("name", nargs="?")
    project.add_argument("path", nargs="?")

    args = parser.parse_args()
    command = args.command or "chat"

    if command in ("chat", "talk"):
        _converse(command)
    elif command == "status":
        _status()
    elif command == "setup-voice":
        _setup_voice()
    elif command == "brain":
        _brain(args.name)
    elif command == "orchestrator":
        _orchestrator(args.name)
    elif command == "provider":
        _provider(args.name)
    elif command == "project":
        _project(args)
    elif command == "sync":
        _sync(args.max)
    elif command == "install-sync":
        _install_sync()
    elif command == "daemon":
        _daemon()
    elif command == "install-daemon":
        _install_daemon()
    elif command == "stop":
        _stop()
    elif command == "ui":
        port = Config.load().dashboard_port
        subprocess.run(["open", f"http://127.0.0.1:{port}"])


def _converse(mode: str) -> None:
    from .io import ConsoleIO, VoiceIO
    from .orchestration import create_orchestrator

    cfg = Config.load()
    if mode == "talk":
        try:
            io = VoiceIO(cfg)
        except RuntimeError as exc:
            sys.exit(str(exc))
    else:
        io = ConsoleIO()
    try:
        asyncio.run(create_orchestrator(cfg, io).run())
    except KeyboardInterrupt:
        print("\n[jarvis] interrupted.")


def _status() -> None:
    from .memory import Memory

    rows = Memory().recent_tasks(limit=15)
    if not rows:
        print("No task history yet.")
        return
    for tid, title, project, brain, status, summary in rows:
        print(f"{tid:>4} [{status:9}] {title} ({brain}, {project})")
        if summary:
            print(f"      {summary[:120]}")


def _brain(name: str | None) -> None:
    cfg = Config.load()
    if not name:
        print(f"Default worker platform: {cfg.brain}")
        return
    cfg.set_brain(name)
    print(f"Default worker platform set to {name}.")


def _orchestrator(name: str | None) -> None:
    cfg = Config.load()
    if not name:
        print(f"Conversation provider: {cfg.models.orchestrator.provider}")
        return
    cfg.set_orchestrator(name)
    print(f"Conversation provider set to {name}. Restart a running daemon to apply it.")


def _provider(name: str) -> None:
    cfg = Config.load()
    cfg.set_all_providers(name)
    print(
        f"Orchestrator, default worker, and sync provider set to {name}. "
        "Restart a running daemon to apply it."
    )


def _project(args) -> None:
    cfg = Config.load()
    if args.action == "add":
        if not args.name or not args.path:
            sys.exit("usage: jarvis project add <name> <path>")
        path = Path(args.path).expanduser().resolve()
        if not path.is_dir():
            sys.exit(f"{path} is not a directory")
        cfg.register_project(args.name, path)
        print(f"Registered '{args.name}' -> {path}")
        return
    if not cfg.projects:
        print(f"No registered projects. Config: {CONFIG_PATH}")
        return
    for name, path in cfg.projects.items():
        print(f"{name:20} {path}")


def _sync(max_sessions: int | None) -> None:
    from .secondbrain import SecondBrain

    cfg = Config.load()
    brain = SecondBrain(cfg)
    print(brain.sync(max_sessions=max_sessions or cfg.second_brain.batch))


def _daemon() -> None:
    from .daemon import run_daemon

    try:
        asyncio.run(run_daemon())
    except KeyboardInterrupt:
        print("\n[daemon] stopped.")


def _stop() -> None:
    label = "com.jarvis.daemon"
    target = f"gui/{os.getuid()}/{label}"
    running = subprocess.run(
        ["launchctl", "print", target], capture_output=True, text=True
    )
    if running.returncode != 0:
        print("Jarvis daemon and dashboard are already stopped.")
        return

    plist = Path.home() / f"Library/LaunchAgents/{label}.plist"
    subprocess.run(["launchctl", "unload", str(plist)], check=True)
    print("Jarvis daemon and dashboard stopped.")


def _remove_legacy_agents(*labels: str) -> None:
    """Unload/remove launchd agents from older installs (renamed labels)."""
    for label in labels:
        plist = Path.home() / f"Library/LaunchAgents/{label}.plist"
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            plist.unlink()


def _install_daemon() -> None:
    import shutil

    _remove_legacy_agents("com.mohammedhasan.jarvis-daemon")
    jarvis_bin = shutil.which("jarvis") or sys.argv[0]
    provider_dirs = {
        str(Path(binary).parent)
        for name in ("claude", "codex")
        if (binary := shutil.which(name))
    }
    path_env = ":".join(sorted(provider_dirs) + [
        "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"
    ])
    logs = JARVIS_HOME / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    plist = Path.home() / "Library/LaunchAgents/com.jarvis.daemon.plist"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.jarvis.daemon</string>
    <key>ProgramArguments</key>
    <array><string>{jarvis_bin}</string><string>daemon</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ProcessType</key><string>Interactive</string>
    <key>EnvironmentVariables</key>
    <dict><key>PATH</key><string>{path_env}</string></dict>
    <key>StandardOutPath</key><string>{logs}/daemon.log</string>
    <key>StandardErrorPath</key><string>{logs}/daemon.log</string>
</dict>
</plist>
""")
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    subprocess.run(["launchctl", "load", str(plist)], check=True)
    print(
        f"Jarvis daemon installed and started (launchd, restarts automatically).\n"
        f"Plist: {plist}\nLogs: {logs}/daemon.log\n"
        "First run: grant Microphone + Input Monitoring when macOS prompts "
        "(System Settings > Privacy & Security)."
    )


def _install_sync() -> None:
    import shutil

    _remove_legacy_agents("com.mohammedhasan.jarvis-sync")
    jarvis_bin = shutil.which("jarvis") or sys.argv[0]
    logs = JARVIS_HOME / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    plist = Path.home() / "Library/LaunchAgents/com.jarvis.sync.plist"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.jarvis.sync</string>
    <key>ProgramArguments</key>
    <array><string>{jarvis_bin}</string><string>sync</string></array>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>30</integer></dict>
    <key>StandardOutPath</key><string>{logs}/sync.log</string>
    <key>StandardErrorPath</key><string>{logs}/sync.log</string>
</dict>
</plist>
""")
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
    subprocess.run(["launchctl", "load", str(plist)], check=True)
    print(f"Daily sync installed (21:30 every day). Plist: {plist}\nLogs: {logs}/sync.log")


def _setup_voice() -> None:
    models = JARVIS_HOME / "models"
    models.mkdir(parents=True, exist_ok=True)
    model = models / "ggml-small.en.bin"

    print("Installing whisper-cpp via Homebrew...")
    subprocess.run(["brew", "install", "whisper-cpp"], check=False)

    if model.exists():
        print(f"Model already present: {model}")
    else:
        print(f"Downloading STT model (~466 MB) to {model} ...")
        subprocess.run(
            ["curl", "-L", "--progress-bar", "-o", str(model), WHISPER_MODEL_URL],
            check=True,
        )

    vad = models / "silero_vad.onnx"
    if not vad.exists():
        print("Downloading Silero VAD model (~2 MB)...")
        subprocess.run(
            ["curl", "-L", "--progress-bar", "-o", str(vad), VAD_MODEL_URL],
            check=True,
        )
    print("Voice setup done. Try: jarvis talk")


if __name__ == "__main__":
    main()

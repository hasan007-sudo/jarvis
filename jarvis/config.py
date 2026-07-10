"""Configuration for Jarvis, stored at ~/.jarvis/config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", str(Path.home() / ".jarvis")))
CONFIG_PATH = JARVIS_HOME / "config.yaml"

DEFAULT_CONFIG: dict = {
    "brain": "claude",
    "codex_bin": "codex",
    "search_roots": [
        "~/Github",
        "~/Projects",
        "~/Developer",
        "~/Documents",
        "~/Desktop",
    ],
    "projects": {},
    "voice": {
        "tts_voice": "Samantha",
        "stt_engine": "whisper",  # whisper | parakeet
        "whisper_bin": "whisper-cli",
        "whisper_model": "~/.jarvis/models/ggml-small.en.bin",
        "hotkey": "<ctrl>+<alt>+j",
        "silence_seconds": 2.0,
        "vad_model": "~/.jarvis/models/silero_vad.onnx",
        "followup_seconds": 6.0,
    },
    "dashboard_port": 8787,
    "second_brain": {
        "vault": "~/Github/Personal/second-brain",
        "batch": 12,
        "commit": True,
    },
}


@dataclass
class VoiceConfig:
    tts_voice: str = "Samantha"
    stt_engine: str = "whisper"
    whisper_bin: str = "whisper-cli"
    whisper_model: Path = Path.home() / ".jarvis/models/ggml-small.en.bin"
    hotkey: str = "<ctrl>+<alt>+j"
    silence_seconds: float = 2.0
    vad_model: Path = Path.home() / ".jarvis/models/silero_vad.onnx"
    followup_seconds: float = 6.0


@dataclass
class SecondBrainConfig:
    vault: Path = Path.home() / "Github/Personal/second-brain"
    batch: int = 12
    commit: bool = True


@dataclass
class Config:
    brain: str = "claude"
    codex_bin: str = "codex"
    search_roots: list[Path] = field(default_factory=list)
    projects: dict[str, Path] = field(default_factory=dict)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    second_brain: SecondBrainConfig = field(default_factory=SecondBrainConfig)
    dashboard_port: int = 8787

    @classmethod
    def load(cls) -> "Config":
        JARVIS_HOME.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text(yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False))
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        merged = {**DEFAULT_CONFIG, **raw}
        v = {**DEFAULT_CONFIG["voice"], **(merged.get("voice") or {})}
        sb = {**DEFAULT_CONFIG["second_brain"], **(merged.get("second_brain") or {})}
        return cls(
            brain=merged.get("brain", "claude"),
            codex_bin=merged.get("codex_bin", "codex"),
            search_roots=[Path(p).expanduser() for p in merged.get("search_roots", [])],
            projects={
                name: Path(p).expanduser()
                for name, p in (merged.get("projects") or {}).items()
            },
            voice=VoiceConfig(
                tts_voice=v["tts_voice"],
                stt_engine=v.get("stt_engine", "whisper"),
                whisper_bin=v["whisper_bin"],
                whisper_model=Path(v["whisper_model"]).expanduser(),
                hotkey=v.get("hotkey", "<ctrl>+<alt>+j"),
                silence_seconds=float(v.get("silence_seconds", 2.0)),
                vad_model=Path(
                    v.get("vad_model", "~/.jarvis/models/silero_vad.onnx")
                ).expanduser(),
                followup_seconds=float(v.get("followup_seconds", 6.0)),
            ),
            dashboard_port=int(merged.get("dashboard_port", 8787)),
            second_brain=SecondBrainConfig(
                vault=Path(sb["vault"]).expanduser(),
                batch=int(sb["batch"]),
                commit=bool(sb["commit"]),
            ),
        )

    def _update(self, key: str, value) -> None:
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        raw[key] = value
        CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False))

    def set_brain(self, brain: str) -> None:
        if brain not in ("claude", "codex"):
            raise ValueError(f"Unknown brain: {brain!r} (expected 'claude' or 'codex')")
        self.brain = brain
        self._update("brain", brain)

    def register_project(self, name: str, path: Path) -> None:
        self.projects[name] = path
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        raw.setdefault("projects", {})[name] = str(path)
        self._update("projects", raw["projects"])

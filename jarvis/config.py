"""Jarvis application and AI-provider configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

JARVIS_HOME = Path(os.environ.get("JARVIS_HOME", str(Path.home() / ".jarvis")))
CONFIG_PATH = JARVIS_HOME / "config.yaml"
MODELS_PATH = JARVIS_HOME / "models.yaml"

DEFAULT_CONFIG: dict = {
    "user_name": "",  # how Jarvis addresses you; defaults to your OS username
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

DEFAULT_MODELS_CONFIG: dict = {
    "orchestrator": {
        "provider": "codex",
        "claude": {"model": "sonnet"},
        "codex": {
            "model": "gpt-5.5",
            "sandbox": "read-only",
            "ephemeral": False,
            "skip_git_repo_check": True,
            "ignore_user_config": False,
            "ignore_rules": False,
        },
    },
    "brain": {
        "provider": "codex",
        "claude": {"model": "sonnet"},
        "codex": {
            "model": "gpt-5.5",
            "sandbox": "workspace-write",
            "ephemeral": False,
            "skip_git_repo_check": True,
            "ignore_user_config": False,
            "ignore_rules": False,
        },
    },
    "sync": {
        "provider": "codex",
        "claude": {"model": "haiku"},
        "codex": {
            "model": "gpt-5.5",
            "sandbox": "read-only",
            "ephemeral": True,
            "skip_git_repo_check": True,
            "ignore_user_config": True,
            "ignore_rules": True,
        },
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
class ClaudeModelConfig:
    model: str


@dataclass
class CodexModelConfig:
    model: str
    sandbox: str
    ephemeral: bool
    skip_git_repo_check: bool
    ignore_user_config: bool
    ignore_rules: bool


@dataclass
class ModelRoleConfig:
    provider: str
    claude: ClaudeModelConfig
    codex: CodexModelConfig


@dataclass
class ModelsConfig:
    orchestrator: ModelRoleConfig
    brain: ModelRoleConfig
    sync: ModelRoleConfig


@dataclass
class Config:
    user_name: str = ""
    brain: str = "codex"
    codex_bin: str = "codex"
    search_roots: list[Path] = field(default_factory=list)
    projects: dict[str, Path] = field(default_factory=dict)
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    second_brain: SecondBrainConfig = field(default_factory=SecondBrainConfig)
    models: ModelsConfig = field(default_factory=lambda: _default_models())
    dashboard_port: int = 8787

    @classmethod
    def load(cls) -> "Config":
        JARVIS_HOME.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text(yaml.safe_dump(DEFAULT_CONFIG, sort_keys=False))
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        merged = {**DEFAULT_CONFIG, **raw}
        models = _load_models(legacy_brain=raw.get("brain"))
        v = {**DEFAULT_CONFIG["voice"], **(merged.get("voice") or {})}
        sb = {**DEFAULT_CONFIG["second_brain"], **(merged.get("second_brain") or {})}
        import getpass

        return cls(
            user_name=merged.get("user_name") or getpass.getuser(),
            brain=models.brain.provider,
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
            models=models,
        )

    def _update(self, key: str, value) -> None:
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        raw[key] = value
        CONFIG_PATH.write_text(yaml.safe_dump(raw, sort_keys=False))

    def set_brain(self, brain: str) -> None:
        if brain not in ("claude", "codex"):
            raise ValueError(f"Unknown brain: {brain!r} (expected 'claude' or 'codex')")
        self.brain = brain
        self.models.brain.provider = brain
        raw = yaml.safe_load(MODELS_PATH.read_text()) or {}
        raw.setdefault("brain", {})["provider"] = brain
        MODELS_PATH.write_text(yaml.safe_dump(raw, sort_keys=False))

    def set_orchestrator(self, provider: str) -> None:
        if provider not in ("claude", "codex"):
            raise ValueError(
                f"Unknown orchestrator: {provider!r} (expected 'claude' or 'codex')"
            )
        self.models.orchestrator.provider = provider
        raw = yaml.safe_load(MODELS_PATH.read_text()) or {}
        raw.setdefault("orchestrator", {})["provider"] = provider
        MODELS_PATH.write_text(yaml.safe_dump(raw, sort_keys=False))

    def set_all_providers(self, provider: str) -> None:
        if provider not in ("claude", "codex"):
            raise ValueError(f"Unknown provider: {provider!r}")
        self.brain = provider
        for role in (self.models.orchestrator, self.models.brain, self.models.sync):
            role.provider = provider
        raw = yaml.safe_load(MODELS_PATH.read_text()) or {}
        for name in ("orchestrator", "brain", "sync"):
            raw.setdefault(name, {})["provider"] = provider
        MODELS_PATH.write_text(yaml.safe_dump(raw, sort_keys=False))

    def register_project(self, name: str, path: Path) -> None:
        self.projects[name] = path
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        raw.setdefault("projects", {})[name] = str(path)
        self._update("projects", raw["projects"])


def _load_models(legacy_brain: str | None = None) -> ModelsConfig:
    if not MODELS_PATH.exists():
        initial = yaml.safe_load(yaml.safe_dump(DEFAULT_MODELS_CONFIG))
        if legacy_brain in ("claude", "codex"):
            initial["brain"]["provider"] = legacy_brain
        MODELS_PATH.write_text(yaml.safe_dump(initial, sort_keys=False))

    raw = yaml.safe_load(MODELS_PATH.read_text()) or {}
    if "orchestrator" not in raw:
        raw["orchestrator"] = yaml.safe_load(
            yaml.safe_dump(DEFAULT_MODELS_CONFIG["orchestrator"])
        )
        MODELS_PATH.write_text(yaml.safe_dump(raw, sort_keys=False))
    roles = {
        name: _load_model_role(name, raw.get(name) or {})
        for name in ("orchestrator", "brain", "sync")
    }
    return ModelsConfig(
        orchestrator=roles["orchestrator"],
        brain=roles["brain"],
        sync=roles["sync"],
    )


def _default_models() -> ModelsConfig:
    return ModelsConfig(
        orchestrator=_load_model_role("orchestrator", {}),
        brain=_load_model_role("brain", {}),
        sync=_load_model_role("sync", {}),
    )


def _load_model_role(name: str, raw: dict) -> ModelRoleConfig:
    defaults = DEFAULT_MODELS_CONFIG[name]
    provider = raw.get("provider", defaults["provider"])
    if provider not in ("claude", "codex"):
        raise ValueError(
            f"Invalid {name}.provider in {MODELS_PATH}: {provider!r}; "
            "expected 'claude' or 'codex'"
        )

    claude = {**defaults["claude"], **(raw.get("claude") or {})}
    codex = {**defaults["codex"], **(raw.get("codex") or {})}
    sandbox = codex["sandbox"]
    if sandbox not in ("read-only", "workspace-write", "danger-full-access"):
        raise ValueError(
            f"Invalid {name}.codex.sandbox in {MODELS_PATH}: {sandbox!r}"
        )
    if not claude.get("model") or not codex.get("model"):
        raise ValueError(f"Both {name} model names are required in {MODELS_PATH}")

    return ModelRoleConfig(
        provider=provider,
        claude=ClaudeModelConfig(model=str(claude["model"])),
        codex=CodexModelConfig(
            model=str(codex["model"]),
            sandbox=sandbox,
            ephemeral=bool(codex["ephemeral"]),
            skip_git_repo_check=bool(codex["skip_git_repo_check"]),
            ignore_user_config=bool(codex["ignore_user_config"]),
            ignore_rules=bool(codex["ignore_rules"]),
        ),
    )

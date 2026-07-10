"""Speech-to-text, fully on-device. Two engines:

- whisper  : whisper.cpp (`whisper-cli`) with a configurable ggml model
- parakeet : NVIDIA Parakeet TDT via parakeet-mlx (Apple Silicon)

Selected by `voice.stt_engine` in ~/.jarvis/config.yaml.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..config import VoiceConfig


class BaseSTT:
    def __init__(self, vcfg: VoiceConfig):
        self.silence_seconds = vcfg.silence_seconds
        self.vad_model = vcfg.vad_model

    def listen_once(self, start_timeout: float | None = None) -> str:
        from .capture import record_utterance

        wav = record_utterance(
            silence_after=self.silence_seconds,
            vad_model=self.vad_model,
            start_timeout=start_timeout,
        )
        if wav is None:
            return ""
        try:
            return self._clean(self.transcribe(wav))
        finally:
            wav.unlink(missing_ok=True)

    def transcribe(self, wav: Path) -> str:
        raise NotImplementedError

    @staticmethod
    def _clean(raw: str) -> str:
        """Drop non-speech markers like [BLANK_AUDIO], (wind blowing)."""
        text = re.sub(r"[\[\(][A-Za-z_ ]+[\]\)]", " ", raw)
        return " ".join(text.split()).strip()


class WhisperSTT(BaseSTT):
    def __init__(self, vcfg: VoiceConfig):
        super().__init__(vcfg)
        self.bin = shutil.which(vcfg.whisper_bin)
        self.model = vcfg.whisper_model
        if not self.bin or not self.model.exists():
            raise RuntimeError(
                "Voice input isn't set up (need whisper-cli and a model). "
                "Run `jarvis setup-voice` first."
            )

    def transcribe(self, wav: Path) -> str:
        out = subprocess.run(
            [self.bin, "-m", str(self.model), "-f", str(wav), "-nt", "-np"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return out.stdout


class ParakeetSTT(BaseSTT):
    """parakeet-mlx CLI (same tool env as jarvis). Model auto-cached by HF."""

    def __init__(self, vcfg: VoiceConfig):
        super().__init__(vcfg)
        candidate = Path(sys.executable).parent / "parakeet-mlx"
        self.bin = str(candidate) if candidate.exists() else shutil.which("parakeet-mlx")
        if not self.bin:
            raise RuntimeError(
                "parakeet-mlx not found in the jarvis tool environment; "
                "reinstall with `--with parakeet-mlx` or set stt_engine: whisper."
            )

    def transcribe(self, wav: Path) -> str:
        with tempfile.TemporaryDirectory(prefix="jarvis_pk_") as tmp:
            subprocess.run(
                [self.bin, str(wav), "--output-format", "txt",
                 "--output-dir", tmp, "--output-template", "out"],
                capture_output=True,
                timeout=120,
            )
            txt = Path(tmp) / "out.txt"
            return txt.read_text() if txt.exists() else ""


def make_stt(vcfg: VoiceConfig) -> BaseSTT:
    if vcfg.stt_engine == "parakeet":
        return ParakeetSTT(vcfg)
    return WhisperSTT(vcfg)

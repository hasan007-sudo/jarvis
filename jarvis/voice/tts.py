"""Text-to-speech via the built-in macOS `say` command. Zero cost, zero RAM."""

from __future__ import annotations

import subprocess

from ..config import VoiceConfig


class Speaker:
    def __init__(self, vcfg: VoiceConfig):
        self.voice = vcfg.tts_voice
        self._proc: subprocess.Popen | None = None

    def speak(self, text: str) -> None:
        self.stop()  # don't overlap utterances
        self._proc = subprocess.Popen(
            ["say", "-v", self.voice, text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    @property
    def is_speaking(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

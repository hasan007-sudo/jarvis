"""User I/O: text console now, voice on top of it.

VoiceIO reuses the console for visibility but speaks replies and listens via
push-to-talk (Enter, then speak; recording stops on silence).
"""

from __future__ import annotations

import asyncio

from .config import Config


class ConsoleIO:
    async def ask(self) -> str:
        try:
            return await asyncio.to_thread(input, "\nyou> ")
        except EOFError:
            return "exit"

    def say(self, text: str) -> None:
        print(f"\njarvis> {text}")

    def notify(self, text: str) -> None:
        print(f"\n[jarvis] {text}")


class VoiceIO(ConsoleIO):
    def __init__(self, cfg: Config):
        from .voice.stt import make_stt
        from .voice.tts import Speaker

        self.speaker = Speaker(cfg.voice)
        self.stt = make_stt(cfg.voice)

    async def ask(self) -> str:
        try:
            await asyncio.to_thread(input, "\n[Enter to speak]")
        except EOFError:
            return "exit"
        print("listening...")
        text = await asyncio.to_thread(self.stt.listen_once)
        print(f"you> {text}")
        return text

    def say(self, text: str) -> None:
        super().say(text)
        self.speaker.speak(text)

    def notify(self, text: str) -> None:
        super().notify(text)
        self.speaker.speak(text)

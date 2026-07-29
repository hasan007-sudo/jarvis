"""Always-on Jarvis daemon with a Wispr-Flow-style global hotkey.

Interaction model:
  1. Press the hotkey (default Ctrl+Option+J) from ANY app.
  2. Pop chime -> Jarvis is LISTENING. Speak; stop for `silence_seconds`
     (default 2.0, Silero VAD) to end your turn.
  3. Morse chime -> Jarvis heard you and is THINKING.
  4. Jarvis SPEAKS its reply.
  5. Tink chime -> follow-up window: the mic reopens for `followup_seconds`
     so you can keep the conversation going WITHOUT pressing the hotkey.
     Stay silent and Jarvis goes idle until the next hotkey press.
Pressing the hotkey while Jarvis is speaking cuts it off (barge-in).

State (idle/listening/thinking/speaking) and task progress are served on a
local dashboard: http://127.0.0.1:<dashboard_port>  (`jarvis ui` opens it).

Installed as a launchd LaunchAgent by `jarvis install-daemon` (KeepAlive,
starts at login). One-time macOS grants required: Microphone + Input
Monitoring/Accessibility for the resolved Jarvis python binary.
"""

from __future__ import annotations

import asyncio
import subprocess
from collections import deque

from .config import Config
from .dashboard import start_dashboard
from .orchestration import create_orchestrator

LISTEN_CHIME = "/System/Library/Sounds/Pop.aiff"    # mic open (hotkey)
ACK_CHIME = "/System/Library/Sounds/Morse.aiff"     # heard you, thinking
FOLLOWUP_CHIME = "/System/Library/Sounds/Tink.aiff" # follow-up window open
ERROR_CHIME = "/System/Library/Sounds/Basso.aiff"   # heard nothing / failed


def _chime(path: str) -> None:
    subprocess.Popen(
        ["afplay", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


class HotkeyVoiceIO:
    """Voice I/O gated on a global hotkey, with a hands-free follow-up window."""

    def __init__(self, cfg: Config):
        from .voice.stt import make_stt
        from .voice.tts import Speaker

        self.cfg = cfg
        self.speaker = Speaker(cfg.voice)
        self.stt = make_stt(cfg.voice)
        self.state = "starting"  # idle | listening | thinking | speaking (see status)
        self.transcript: deque = deque(maxlen=40)
        self._trigger = asyncio.Event()
        self._followup = False
        self._listener = None

    @property
    def status(self) -> str:
        if self.speaker.is_speaking:
            return "speaking"
        return self.state

    def start_hotkey(self, loop: asyncio.AbstractEventLoop) -> None:
        from pynput import keyboard

        def on_activate() -> None:
            self.speaker.stop()  # barge-in: shut up and listen
            loop.call_soon_threadsafe(self._trigger.set)

        self._listener = keyboard.GlobalHotKeys({self.cfg.voice.hotkey: on_activate})
        self._listener.daemon = True
        self._listener.start()

    async def ask(self) -> str:
        while True:
            # Hands-free follow-up: after a reply, reopen the mic briefly.
            if self._followup and not self._trigger.is_set():
                while self.speaker.is_speaking and not self._trigger.is_set():
                    await asyncio.sleep(0.1)  # don't record our own voice
                if not self._trigger.is_set():
                    _chime(FOLLOWUP_CHIME)
                    self.state = "listening"
                    text = await self._capture(self.cfg.voice.followup_seconds)
                    if text:
                        return text
                    self._followup = False  # window closed — back to hotkey

            self.state = "idle"
            await self._trigger.wait()
            self._trigger.clear()
            _chime(LISTEN_CHIME)
            self.state = "listening"
            text = await self._capture(None)
            if text:
                return text
            _chime(ERROR_CHIME)

    async def _capture(self, start_timeout: float | None) -> str:
        try:
            text = await asyncio.to_thread(self.stt.listen_once, start_timeout)
        except Exception as exc:
            print(f"[daemon] capture failed: {exc}", flush=True)
            return ""
        if not text:
            return ""
        _chime(ACK_CHIME)
        self.state = "thinking"
        self._followup = True
        self.transcript.append(["you", text])
        print(f"you> {text}", flush=True)
        return text

    def say(self, text: str) -> None:
        self.transcript.append(["jarvis", text])
        print(f"jarvis> {text}", flush=True)
        self.speaker.speak(text)

    def notify(self, text: str) -> None:
        self.transcript.append(["notice", text])
        print(f"[jarvis] {text}", flush=True)
        self.speaker.speak(text)


async def run_daemon() -> None:
    cfg = Config.load()
    io = HotkeyVoiceIO(cfg)
    io.start_hotkey(asyncio.get_running_loop())
    orch = create_orchestrator(cfg, io)
    start_dashboard(orch, io, cfg.dashboard_port)
    print(
        f"[daemon] up — hotkey {cfg.voice.hotkey}, "
        f"end-of-turn {cfg.voice.silence_seconds}s, "
        f"follow-up window {cfg.voice.followup_seconds}s, "
        f"dashboard http://127.0.0.1:{cfg.dashboard_port}",
        flush=True,
    )
    await orch.run()

"""Microphone capture with end-of-speech detection.

Uses Silero VAD (tiny neural voice-activity model, ONNX) when the model file
is present — robust in noisy rooms. Falls back to an RMS energy threshold
otherwise. Recording ends after `silence_after` seconds of non-speech
following detected speech.
"""

from __future__ import annotations

import tempfile
import wave
from pathlib import Path

VAD_THRESHOLD = 0.5


class SileroVAD:
    """Minimal ONNX wrapper; handles both v4 (h/c) and v5 (state) models."""

    def __init__(self, model_path: Path):
        import numpy as np
        import onnxruntime as ort

        self._np = np
        self.sess = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        names = {i.name for i in self.sess.get_inputs()}
        self.v5 = "state" in names
        self.reset()

    def reset(self) -> None:
        np = self._np
        if self.v5:
            self.state = np.zeros((2, 1, 128), dtype=np.float32)
            self._ctx = np.zeros(64, dtype=np.float32)  # v5 rolling context
        else:
            self.h = np.zeros((2, 1, 64), dtype=np.float32)
            self.c = np.zeros((2, 1, 64), dtype=np.float32)

    def prob(self, chunk) -> float:
        """Speech probability for one 512-sample float32 chunk @ 16 kHz."""
        np = self._np
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        sr = np.array(16000, dtype=np.int64)
        if self.v5:
            # v5 expects the last 64 samples of the previous chunk prepended.
            x = np.concatenate([self._ctx, chunk]).reshape(1, -1)
            out, self.state = self.sess.run(
                None, {"input": x, "state": self.state, "sr": sr}
            )
            self._ctx = chunk[-64:]
        else:
            out, self.h, self.c = self.sess.run(
                None, {"input": chunk.reshape(1, -1), "sr": sr, "h": self.h, "c": self.c}
            )
        return float(out.reshape(-1)[0])


def record_utterance(
    max_seconds: float = 60.0,
    silence_after: float = 2.0,
    threshold: float = 0.012,
    samplerate: int = 16000,
    vad_model: Path | None = None,
    start_timeout: float | None = None,
) -> Path | None:
    """Record until the speaker goes quiet for `silence_after` seconds.

    If `start_timeout` is set and no speech starts within that window,
    returns None (used for the hands-free follow-up window).
    """
    import numpy as np
    import sounddevice as sd

    vad = None
    if vad_model and Path(vad_model).exists():
        try:
            vad = SileroVAD(Path(vad_model))
        except Exception:
            vad = None  # onnxruntime missing/broken -> energy fallback

    block = 512 if vad else int(samplerate * 0.1)
    block_secs = block / samplerate

    def is_speech(mono) -> bool:
        if vad:
            return vad.prob(mono) >= VAD_THRESHOLD
        return float(np.sqrt(np.mean(np.square(mono)))) > threshold

    frames: list = []
    started = False
    silence = 0.0
    total = 0.0

    with sd.InputStream(
        samplerate=samplerate, channels=1, dtype="float32", blocksize=block
    ) as stream:
        while total < max_seconds:
            data, _ = stream.read(block)
            frames.append(data.copy())
            total += block_secs
            if is_speech(data[:, 0]):
                started = True
                silence = 0.0
            elif started:
                silence += block_secs
                if silence >= silence_after:
                    break
            elif start_timeout is not None and total >= start_timeout:
                return None  # nobody spoke — close the follow-up window

    audio = np.concatenate(frames)[:, 0]
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    fd, name = tempfile.mkstemp(suffix=".wav", prefix="jarvis_")
    path = Path(name)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm.tobytes())
    return path

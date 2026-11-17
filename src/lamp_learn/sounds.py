"""The coach's two short sounds, made here as WAVs the lamp takes.

The lamp keeps a computer's clips (`upload_sound`) only as 16-bit mono PCM at
24 kHz, with no sample past -12 dBFS and no 10 ms louder than -30 dBFS RMS: a
steady tone may peak near -27 dBFS at most. These stay under -29 dBFS RMS.
"""

from __future__ import annotations

import io
import math
import struct
import wave

RATE = 24_000
# A sine's RMS is its peak / sqrt(2): this peak keeps every 10 ms under -30 dBFS RMS.
PEAK = int(32_767 * 10 ** (-30.5 / 20) * math.sqrt(2))

RIGHT = "learn-right"
MISS = "learn-miss"


def _notes(notes: list[tuple[float, float]], *, gap_s: float = 0.02) -> bytes:
    """Sine notes (Hz, seconds), each with a soft attack and decay, as WAV bytes."""
    frames = bytearray()
    for hz, seconds in notes:
        count = int(RATE * seconds)
        attack = int(RATE * 0.01)
        for n in range(count):
            # A quick rise, then an exponential fall: a bell, not a beep.
            envelope = min(1.0, n / attack) * math.exp(-3.0 * n / count)
            frames += struct.pack("<h", int(PEAK * envelope * math.sin(2 * math.pi * hz * n / RATE)))
        frames += b"\x00\x00" * int(RATE * gap_s)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(bytes(frames))
    return out.getvalue()


def right_chime() -> bytes:
    """Two rising notes, C6 then G6: bright and short."""
    return _notes([(1046.5, 0.12), (1568.0, 0.28)])


def miss_tone() -> bytes:
    """Two falling low notes, E4 then C4: gentle, not a buzzer."""
    return _notes([(329.6, 0.18), (261.6, 0.32)])


CLIPS = {RIGHT: right_chime, MISS: miss_tone}

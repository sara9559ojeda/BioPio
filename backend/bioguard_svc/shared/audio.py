"""Synthetic field audio for the demo.

Each species is rendered as short frequency-modulated calls inside its
frequency band, over background noise (and stream noise near water). The
acoustic service's classifier then has to find them again from the raw PCM.
"""

from __future__ import annotations

import io
import wave
from collections.abc import Mapping, Sequence

import numpy as np

from bioguard_svc.shared.scenario import SPECIES_BY_CODE

SAMPLE_RATE_HZ = 22_050
# Bands no species uses: sounds there are "unidentified" for the model.
UNKNOWN_BANDS_HZ: Sequence[tuple[float, float]] = ((700, 1000), (10_000, 10_700))


def _call(duration_s: float, low_hz: float, high_hz: float, amplitude: float, rng: np.random.Generator) -> np.ndarray:
    n = int(duration_s * SAMPLE_RATE_HZ)
    margin = (high_hz - low_hz) * 0.15
    f_start = rng.uniform(low_hz + margin, high_hz - margin)
    f_end = np.clip(f_start + rng.uniform(-margin, margin), low_hz + margin, high_hz - margin)
    freq = np.linspace(f_start, f_end, n)
    phase = 2 * np.pi * np.cumsum(freq) / SAMPLE_RATE_HZ
    envelope = np.hanning(n)
    return amplitude * envelope * np.sin(phase + rng.uniform(0, 2 * np.pi))


def synthesize_clip(
    calls_per_species: Mapping[str, int],
    duration_s: float,
    rng: np.random.Generator,
    near_stream: bool = False,
    unknown_events: int = 0,
) -> np.ndarray:
    """Return float32 mono samples in [-1, 1]."""
    total = int(duration_s * SAMPLE_RATE_HZ)
    signal = rng.normal(0, 0.006, total)
    if near_stream:
        # Low-frequency stream noise (smoothed white noise).
        stream = np.convolve(rng.normal(0, 0.05, total), np.ones(12) / 12, mode="same")
        signal += stream

    def place(sound: np.ndarray) -> None:
        if sound.size >= total:
            return
        start = int(rng.integers(0, total - sound.size))
        signal[start : start + sound.size] += sound

    for code, count in calls_per_species.items():
        species = SPECIES_BY_CODE[code]
        for _ in range(count):
            place(_call(rng.uniform(0.12, 0.32), species.band_low_hz, species.band_high_hz, rng.uniform(0.12, 0.35), rng))

    for _ in range(unknown_events):
        low, high = UNKNOWN_BANDS_HZ[int(rng.integers(0, len(UNKNOWN_BANDS_HZ)))]
        place(_call(rng.uniform(0.15, 0.3), low, high, rng.uniform(0.15, 0.3), rng))

    peak = float(np.max(np.abs(signal))) or 1.0
    if peak > 0.95:
        signal *= 0.95 / peak
    return signal.astype(np.float32)


def to_pcm16(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def to_wav(samples: np.ndarray, sample_rate: int = SAMPLE_RATE_HZ) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(to_pcm16(samples))
    return buffer.getvalue()


def decode_audio(data: bytes, declared_sample_rate: int) -> tuple[np.ndarray, int]:
    """Accept raw PCM s16le or a WAV file. Returns (float samples, sample rate)."""
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getsampwidth() != 2:
                raise ValueError("Solo se admite WAV PCM de 16 bits")
            rate = wav.getframerate()
            channels = wav.getnchannels()
            frames = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32)
        if channels > 1:
            frames = frames.reshape(-1, channels).mean(axis=1)
        return frames / 32768.0, rate
    if len(data) % 2:
        data = data[:-1]
    samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    return samples, declared_sample_rate or SAMPLE_RATE_HZ

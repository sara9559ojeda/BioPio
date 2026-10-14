"""Spectral band classifier (demo model).

It finds acoustic events in a spectrogram and assigns them to a species only
when the energy concentrates in that species' band. Events it does not
recognize are counted as "unidentified" instead of being guessed.

A production deployment would plug a trained model (e.g. a CNN over
mel-spectrograms) behind the same gRPC contract; callers would not change.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from bioguard_svc.shared.scenario import SPECIES, SpeciesDef

MODEL_VERSION = "bioguard-bandnet-0.4 (modelo espectral de demostración)"

N_FFT = 1024
HOP = 512
SNR_THRESHOLD_DB = 9.0
MIN_EVENT_FRAMES = 3
MIN_CONFIDENCE = 0.6
MIN_BAND_CONCENTRATION = 0.45


@dataclass(frozen=True)
class RawDetection:
    species_code: str
    offset_s: float
    confidence: float
    peak_frequency_hz: float


@dataclass(frozen=True)
class ClassificationResult:
    detections: list[RawDetection]
    unidentified_events: int
    duration_s: float


def _spectrogram(samples: np.ndarray) -> np.ndarray:
    if samples.size < N_FFT:
        samples = np.pad(samples, (0, N_FFT - samples.size))
    frames = 1 + (samples.size - N_FFT) // HOP
    window = np.hanning(N_FFT).astype(np.float32)
    index = np.arange(N_FFT)[None, :] + HOP * np.arange(frames)[:, None]
    spectrum = np.fft.rfft(samples[index] * window, axis=1)
    return (np.abs(spectrum) ** 2).astype(np.float64)  # (frames, bins)


def _events(active: np.ndarray) -> list[tuple[int, int]]:
    """Group active frames into (start, end) runs allowing a 1-frame gap."""
    runs: list[tuple[int, int]] = []
    start = None
    gap = 0
    for i, on in enumerate(active):
        if on:
            if start is None:
                start = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap > 1:
                runs.append((start, i - gap))
                start, gap = None, 0
    if start is not None:
        runs.append((start, len(active) - 1 - gap))
    return [(a, b) for a, b in runs if b - a + 1 >= MIN_EVENT_FRAMES]


class SpectralClassifier:
    def __init__(self, species: list[SpeciesDef] | None = None) -> None:
        self._species = species or SPECIES

    def classify(self, samples: np.ndarray, sample_rate: int) -> ClassificationResult:
        duration = samples.size / sample_rate if sample_rate else 0.0
        if samples.size == 0:
            return ClassificationResult([], 0, 0.0)

        power = _spectrogram(samples.astype(np.float32))
        freqs = np.fft.rfftfreq(N_FFT, 1 / sample_rate)
        frame_s = HOP / sample_rate
        nyquist = sample_rate / 2

        # Broadband energy (300 Hz - Nyquist) to measure band concentration.
        broadband_mask = freqs >= 300
        broadband = power[:, broadband_mask].sum(axis=1) + 1e-12

        detections: list[RawDetection] = []
        claimed = np.zeros(power.shape[0], dtype=bool)

        for species in self._species:
            if species.band_high_hz >= nyquist:
                continue
            mask = (freqs >= species.band_low_hz) & (freqs <= species.band_high_hz)
            band = power[:, mask]
            energy = band.sum(axis=1) + 1e-12
            floor = float(np.median(energy))
            snr_db = 10 * np.log10(energy / max(floor, 1e-12))
            active = snr_db > SNR_THRESHOLD_DB
            for start, end in _events(active):
                segment = slice(start, end + 1)
                concentration = float(energy[segment].sum() / broadband[segment].sum())
                if concentration < MIN_BAND_CONCENTRATION:
                    continue
                peak_snr = float(snr_db[segment].max())
                length = end - start + 1
                confidence = float(
                    np.clip(1 / (1 + np.exp(-(peak_snr - 14) / 4)) * min(1.0, 0.55 + length / 12) * (0.6 + 0.4 * concentration), 0, 0.99)
                )
                if confidence < MIN_CONFIDENCE:
                    continue
                band_power = band[segment].sum(axis=0)
                peak_hz = float(freqs[mask][int(np.argmax(band_power))])
                detections.append(RawDetection(species.code, round(start * frame_s, 3), round(confidence, 3), peak_hz))
                claimed[segment] = True

        # Unrecognized events: loud broadband frames not explained by any species.
        floor = float(np.median(broadband))
        loud = 10 * np.log10(broadband / max(floor, 1e-12)) > SNR_THRESHOLD_DB
        unidentified = len(_events(loud & ~claimed))

        detections.sort(key=lambda d: d.offset_s)
        return ClassificationResult(detections, unidentified, duration)

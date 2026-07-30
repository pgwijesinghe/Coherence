"""Synthetic multi-tone signal generator, used when no NI-DAQmx hardware is present.

Streams continuously at real (wall-clock) pace on a background thread so the rest of
the pipeline exercises exactly the same timing behavior it would see from real hardware.
Each configured channel gets a slowly-varying amplitude/phase envelope plus noise, so the
UI has something realistic to track.
"""

from __future__ import annotations

import hashlib
import threading
import time

import numpy as np

from coherence.config import AcquisitionConfig, ChannelConfig
from coherence.daq.base import AcquisitionBackend, ChunkCallback


def _seed_from_name(name: str) -> np.random.Generator:
    digest = hashlib.sha256(name.encode()).digest()[:8]
    return np.random.default_rng(int.from_bytes(digest, "little"))


class SimulatedBackend(AcquisitionBackend):
    def __init__(
        self,
        acquisition: AcquisitionConfig,
        channels: list[ChannelConfig],
        chunk_size: int = 512,
        noise_std: float = 0.01,
        animate: bool = True,
    ):
        self._fs = acquisition.sample_rate_hz
        self._channels = list(channels)
        self._num_channels = max((c.input_channel for c in channels), default=0) + 1
        self._chunk_size = chunk_size
        self._noise_std = noise_std
        self._animate = animate

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._sample_counter = 0

        self._true_amp: dict[str, float] = {}
        self._true_phase0: dict[str, float] = {}
        self._amp_mod_period_s: dict[str, float] = {}
        self._phase_drift_hz: dict[str, float] = {}
        for ch in self._channels:
            rng = _seed_from_name(ch.name)
            self._true_amp[ch.name] = float(rng.uniform(0.3, 1.0))
            self._true_phase0[ch.name] = float(rng.uniform(-np.pi, np.pi))
            self._amp_mod_period_s[ch.name] = float(rng.uniform(4.0, 12.0))
            self._phase_drift_hz[ch.name] = float(rng.uniform(-0.05, 0.05))

    @property
    def sample_rate_hz(self) -> float:
        return self._fs

    @property
    def num_channels(self) -> int:
        return self._num_channels

    def start(self, on_chunk: ChunkCallback) -> None:
        if self._thread is not None:
            raise RuntimeError("already started")
        self._stop_event.clear()
        self._sample_counter = 0
        self._thread = threading.Thread(
            target=self._run, args=(on_chunk,), name="SimulatedBackend", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self, on_chunk: ChunkCallback) -> None:
        start_wall = time.monotonic()
        while not self._stop_event.is_set():
            n0 = self._sample_counter
            t = (n0 + np.arange(self._chunk_size)) / self._fs
            out = np.zeros((self._chunk_size, self._num_channels), dtype=np.float64)

            for ch in self._channels:
                amp0 = self._true_amp[ch.name]
                if self._animate:
                    period = self._amp_mod_period_s[ch.name]
                    envelope = amp0 * (1.0 + 0.15 * np.sin(2 * np.pi * t / period))
                    drift = 2 * np.pi * self._phase_drift_hz[ch.name] * t
                else:
                    envelope = amp0
                    drift = 0.0
                phase = self._true_phase0[ch.name] + drift
                out[:, ch.input_channel] += envelope * np.sin(2 * np.pi * ch.frequency_hz * t + phase)

            if self._noise_std > 0:
                out += np.random.default_rng().normal(0.0, self._noise_std, out.shape)

            on_chunk(out)
            self._sample_counter += self._chunk_size

            target = start_wall + self._sample_counter / self._fs
            sleep_for = target - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)

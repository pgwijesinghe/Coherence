"""Thread-safe buffer between the FFT worker thread and the Qt UI thread.

The pipeline can emit results at 50-500+ Hz depending on block size/overlap; painting
a Qt widget on every single one would stall the event loop. Instead the worker thread
just appends here (cheap), and a QTimer on the GUI thread pulls a snapshot at a fixed
~30-60 Hz paint rate -- the two rates are intentionally decoupled.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np

from coherence.dsp.fft_engine import BlockResult


@dataclass(slots=True)
class ChannelSeries:
    t: np.ndarray
    amplitude: np.ndarray
    phase_rad: np.ndarray
    x: np.ndarray
    y: np.ndarray


class LiveDataStore:
    def __init__(self, history_len: int = 4000):
        self._history_len = history_len
        self._lock = threading.Lock()
        self._t: dict[str, deque] = {}
        self._amp: dict[str, deque] = {}
        self._phase: dict[str, deque] = {}
        self._x: dict[str, deque] = {}
        self._y: dict[str, deque] = {}
        self._latest_spectra: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    def reset(self) -> None:
        with self._lock:
            self._t.clear()
            self._amp.clear()
            self._phase.clear()
            self._x.clear()
            self._y.clear()
            self._latest_spectra.clear()

    def ingest(self, result: BlockResult) -> None:
        with self._lock:
            for name, ch in result.channels.items():
                self._t.setdefault(name, deque(maxlen=self._history_len)).append(result.timestamp_s)
                self._amp.setdefault(name, deque(maxlen=self._history_len)).append(ch.amplitude)
                self._phase.setdefault(name, deque(maxlen=self._history_len)).append(ch.phase_rad)
                self._x.setdefault(name, deque(maxlen=self._history_len)).append(ch.x)
                self._y.setdefault(name, deque(maxlen=self._history_len)).append(ch.y)
            for input_ch, spec in result.spectra.items():
                self._latest_spectra[input_ch] = (spec.freqs_hz, spec.magnitude_db)

    def latest(self) -> dict[str, tuple[float, float, float, float]]:
        """Just the newest (amplitude, phase_rad, x, y) per channel -- O(channels), no
        array copies. The numeric read-out table only needs this; building full
        history arrays 30x/second for it was measurable GUI-thread waste."""
        with self._lock:
            out = {}
            for name, amps in self._amp.items():
                if amps:
                    out[name] = (amps[-1], self._phase[name][-1], self._x[name][-1], self._y[name][-1])
            return out

    def latest_spectra(self) -> dict[int, tuple[np.ndarray, np.ndarray]]:
        with self._lock:
            return dict(self._latest_spectra)

    def snapshot(self) -> tuple[dict[str, ChannelSeries], dict[int, tuple[np.ndarray, np.ndarray]]]:
        with self._lock:
            series = {
                name: ChannelSeries(
                    t=np.fromiter(self._t[name], dtype=np.float64),
                    amplitude=np.fromiter(self._amp[name], dtype=np.float64),
                    phase_rad=np.fromiter(self._phase[name], dtype=np.float64),
                    x=np.fromiter(self._x[name], dtype=np.float64),
                    y=np.fromiter(self._y[name], dtype=np.float64),
                )
                for name in self._t
            }
            spectra = dict(self._latest_spectra)
        return series, spectra

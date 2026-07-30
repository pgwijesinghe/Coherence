"""Acquisition backend interface.

Both the simulated generator and the real NI-DAQmx backend implement this so the
rest of the pipeline (ring buffer, FFT engine, UI) never needs to know which one
is running underneath.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Protocol

import numpy as np

ChunkCallback = Callable[[np.ndarray], None]
"""Called with a new chunk of raw samples, shape (n_new_samples, n_channels)."""


class AcquisitionBackend(ABC):
    """Continuous multi-channel analog input source."""

    @property
    @abstractmethod
    def sample_rate_hz(self) -> float: ...

    @property
    @abstractmethod
    def num_channels(self) -> int: ...

    @abstractmethod
    def start(self, on_chunk: ChunkCallback) -> None:
        """Begin continuous acquisition; on_chunk is invoked from a background thread."""

    @abstractmethod
    def stop(self) -> None:
        """Stop acquisition and release any hardware resources.

        Must not raise for errors that occurred earlier during acquisition (see
        `drain_errors`) -- stopping cleanly and reporting a past error are separate
        concerns, and conflating them means a single transient error can also break
        the ability to shut down.
        """

    def drain_errors(self) -> list[BaseException]:
        """Non-fatal errors observed since the last call (e.g. a driver-level read
        overrun) -- acquisition is still running underneath; the caller decides
        whether/how to surface these. Default: none (only NIDaqBackend overrides this)."""
        return []

    def __enter__(self) -> "AcquisitionBackend":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


class BackendFactory(Protocol):
    def __call__(self) -> AcquisitionBackend: ...

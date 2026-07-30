"""Thread-safe multi-channel circular buffer bridging the acquisition callback thread
(single writer) and the FFT worker thread (single reader).

Positions are absolute sample counts since acquisition start, not indices mod capacity --
that's what lets the FFT worker request overlapping blocks (e.g. hop = block_size/2)
without any special-casing.
"""

from __future__ import annotations

import threading

import numpy as np


class BufferOverrunError(RuntimeError):
    """Raised when the reader falls far enough behind that data was overwritten."""


class RingBuffer:
    def __init__(self, num_channels: int, capacity_samples: int):
        self._data = np.zeros((capacity_samples, num_channels), dtype=np.float64)
        self._capacity = capacity_samples
        self._num_channels = num_channels
        self._write_pos = 0
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def write_pos(self) -> int:
        with self._lock:
            return self._write_pos

    def push(self, chunk: np.ndarray) -> None:
        if chunk.ndim != 2 or chunk.shape[1] != self._num_channels:
            raise ValueError(f"expected shape (n, {self._num_channels}), got {chunk.shape}")
        n = chunk.shape[0]
        if n > self._capacity:
            raise ValueError("chunk larger than ring buffer capacity")

        with self._lock:
            start = self._write_pos % self._capacity
            end = start + n
            if end <= self._capacity:
                self._data[start:end] = chunk
            else:
                first = self._capacity - start
                self._data[start:] = chunk[:first]
                self._data[: end - self._capacity] = chunk[first:]
            self._write_pos += n

    def try_read_block(self, read_pos: int, block_size: int) -> np.ndarray | None:
        """Returns a copy of [read_pos, read_pos+block_size) if fully available, else None.

        Raises BufferOverrunError if read_pos is older than what's still retained --
        the caller should resynchronize (typically by jumping read_pos to write_pos).
        """
        with self._lock:
            if read_pos + block_size > self._write_pos:
                return None
            if self._write_pos - read_pos > self._capacity:
                raise BufferOverrunError(
                    f"reader is {self._write_pos - read_pos} samples behind "
                    f"writer (capacity {self._capacity}); data was overwritten"
                )
            start = read_pos % self._capacity
            end = start + block_size
            if end <= self._capacity:
                return self._data[start:end].copy()
            first = self._capacity - start
            out = np.empty((block_size, self._num_channels), dtype=np.float64)
            out[:first] = self._data[start:]
            out[first:] = self._data[: end - self._capacity]
            return out

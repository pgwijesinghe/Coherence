"""Structured HDF5 logging of lock-in results, one resizable dataset per channel."""

from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np

from coherence.config import LockinConfig
from coherence.dsp.fft_engine import BlockResult

_CHUNK_ROWS = 4096


class HDF5ResultLogger:
    def __init__(self, path: str | Path, config: LockinConfig):
        self._path = Path(path)
        self._file = h5py.File(self._path, "w")
        self._file.attrs["sample_rate_hz"] = config.acquisition.sample_rate_hz
        self._file.attrs["block_size"] = config.acquisition.block_size
        self._file.attrs["window"] = config.acquisition.window
        self._file.attrs["overlap_fraction"] = config.acquisition.overlap_fraction

        self._datasets: dict[str, h5py.Dataset] = {}
        self._row_counts: dict[str, int] = {}
        for ch in config.channels:
            grp = self._file.create_group(ch.name)
            grp.attrs["frequency_hz"] = ch.frequency_hz
            grp.attrs["input_channel"] = ch.input_channel
            ds = grp.create_dataset(
                "samples",
                shape=(0,),
                maxshape=(None,),
                chunks=(_CHUNK_ROWS,),
                dtype=np.dtype(
                    [
                        ("timestamp_s", "f8"),
                        ("amplitude", "f8"),
                        ("phase_rad", "f8"),
                        ("x", "f8"),
                        ("y", "f8"),
                    ]
                ),
            )
            self._datasets[ch.name] = ds
            self._row_counts[ch.name] = 0

    def append(self, result: BlockResult) -> None:
        for name, ch_result in result.channels.items():
            ds = self._datasets.get(name)
            if ds is None:
                continue
            row = self._row_counts[name]
            ds.resize((row + 1,))
            ds[row] = (
                result.timestamp_s,
                ch_result.amplitude,
                ch_result.phase_rad,
                ch_result.x,
                ch_result.y,
            )
            self._row_counts[name] = row + 1

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "HDF5ResultLogger":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

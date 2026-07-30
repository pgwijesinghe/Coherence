import h5py

from coherence.config import default_config
from coherence.dsp.fft_engine import FFTLockinEngine


def test_logger_appends_rows_matching_engine_output(tmp_path):
    from coherence.logging.hdf5_logger import HDF5ResultLogger

    cfg = default_config()
    engine = FFTLockinEngine(cfg)
    n = cfg.acquisition.block_size

    import numpy as np

    fs = cfg.acquisition.sample_rate_hz
    t = np.arange(n) / fs
    block = np.zeros((n, 3))
    for ch in cfg.channels:
        block[:, ch.input_channel] += np.sin(2 * np.pi * ch.frequency_hz * t)

    path = tmp_path / "log.h5"
    with HDF5ResultLogger(path, cfg) as logger:
        for i in range(3):
            result = engine.process(block, block_start_sample=i * n, timestamp_s=i * 0.01)
            logger.append(result)
        logger.flush()

    with h5py.File(path, "r") as f:
        assert f.attrs["sample_rate_hz"] == cfg.acquisition.sample_rate_hz
        for ch in cfg.channels:
            ds = f[ch.name]["samples"]
            assert ds.shape[0] == 3
            assert ds["amplitude"][0] > 0.9  # unit-amplitude tone, coherent bin

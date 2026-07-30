"""Pre-flight validation tests for NIDaqBackend -- these catch the misconfigurations
that actually caused real failures during hardware bring-up: a device name that
doesn't match anything currently connected, a channel that doesn't exist on its
device, and a sample rate above what the connected device (or the slowest of several
connected devices) supports.

Only exercises `_validate_against_detected_hardware`, which runs before any
DAQmx task is opened -- no hardware is actually acquired from here.
"""

import pytest

from coherence.config import AcquisitionConfig
from coherence.daq import discovery

pytestmark = pytest.mark.skipif(
    not discovery.nidaqmx_available(), reason="nidaqmx not installed on this machine"
)


@pytest.fixture()
def connected_device():
    devices = discovery.list_devices()
    if not devices:
        pytest.skip("no NI device currently connected")
    return devices[0]


def test_rejects_device_not_currently_connected(connected_device):
    from coherence.daq.nidaq_backend import NIDaqBackend

    acq = AcquisitionConfig(ai_channels=("ThisDeviceDoesNotExist/ai0",))
    backend = NIDaqBackend(acq)
    with pytest.raises(RuntimeError, match="not found"):
        backend._validate_against_detected_hardware()


def test_rejects_channel_that_does_not_exist_on_its_device(connected_device):
    from coherence.daq.nidaq_backend import NIDaqBackend

    bogus_channel = f"{connected_device.name}/ai{len(connected_device.ai_channel_names) + 10}"
    acq = AcquisitionConfig(ai_channels=(bogus_channel,))
    backend = NIDaqBackend(acq)
    with pytest.raises(RuntimeError, match="has no channel"):
        backend._validate_against_detected_hardware()


def test_rejects_sample_rate_above_device_max(connected_device):
    from coherence.daq.nidaq_backend import NIDaqBackend

    if connected_device.ai_max_multi_chan_rate_hz is None:
        pytest.skip("device doesn't report a max rate")
    acq = AcquisitionConfig(
        ai_channels=(connected_device.ai_channel_names[0],),
        sample_rate_hz=connected_device.ai_max_multi_chan_rate_hz * 4,
    )
    backend = NIDaqBackend(acq)
    with pytest.raises(RuntimeError, match="exceeds"):
        backend._validate_against_detected_hardware()


def test_rejects_device_missing_from_a_multi_device_list(connected_device):
    """One real device plus one that doesn't exist -- the real one shouldn't mask
    the missing one, since both are supposed to be acquired together."""
    from coherence.daq.nidaq_backend import NIDaqBackend

    acq = AcquisitionConfig(
        ai_channels=(connected_device.ai_channel_names[0], "ThisDeviceDoesNotExist/ai0"),
    )
    backend = NIDaqBackend(acq)
    with pytest.raises(RuntimeError, match="not found"):
        backend._validate_against_detected_hardware()


def test_accepts_a_config_that_matches_the_connected_device(connected_device):
    from coherence.daq.nidaq_backend import NIDaqBackend

    n = min(1, len(connected_device.ai_channel_names))
    acq = AcquisitionConfig(
        ai_channels=tuple(connected_device.ai_channel_names[:n]),
        sample_rate_hz=min(1000.0, connected_device.ai_max_multi_chan_rate_hz or 1000.0),
    )
    backend = NIDaqBackend(acq)
    backend._validate_against_detected_hardware()  # should not raise


def test_stop_does_not_raise_a_previously_queued_error():
    """Regression test: a real run hit a DAQmx read-overrun mid-acquisition, and the
    *stop* call afterward (including the one Qt makes from closeEvent on window close)
    also crashed because stop() re-raised the queued error. Stopping must always
    succeed regardless of what happened earlier during acquisition."""
    from coherence.daq.nidaq_backend import NIDaqBackend

    acq = AcquisitionConfig(ai_channels=("Dev1/ai0",))
    backend = NIDaqBackend(acq)
    backend._error_queue.put(RuntimeError("simulated DAQmx read overrun"))

    backend.stop()  # must not raise


def test_drain_errors_returns_and_clears_queued_errors():
    from coherence.daq.nidaq_backend import NIDaqBackend

    acq = AcquisitionConfig(ai_channels=("Dev1/ai0",))
    backend = NIDaqBackend(acq)
    backend._error_queue.put(RuntimeError("first"))
    backend._error_queue.put(RuntimeError("second"))

    errors = backend.drain_errors()
    assert [str(e) for e in errors] == ["first", "second"]
    assert backend.drain_errors() == []  # queue is now empty, and draining again is safe


def test_default_driver_buffer_has_several_seconds_of_slack():
    """Too little onboard buffer is what turned a transient GIL/scheduling stall into
    a hard read-overrun failure in the first place."""
    from coherence.daq.nidaq_backend import NIDaqBackend

    acq = AcquisitionConfig(sample_rate_hz=51_200.0, ai_channels=("Dev1/ai0",))
    backend = NIDaqBackend(acq, callback_chunk_size=512)
    assert backend._driver_buffer_samples / acq.sample_rate_hz >= 3.0


# -- multi-device read loop (the sync steps themselves are in test_sync.py, shared
# with the AO stimulus generator) -------------------------------------------------


def _backend():
    from coherence.daq.nidaq_backend import NIDaqBackend

    acq = AcquisitionConfig(ai_channels=("Dev1/ai0", "Dev2/ai0"))
    return NIDaqBackend(acq)


class _FakeReader:
    """Stands in for nidaqmx.stream_readers.AnalogMultiChannelReader: fills the
    caller's buffer with a fixed, recognizable value per device instead of reading
    real hardware."""

    def __init__(self, fill_value: float):
        self._fill_value = fill_value

    def read_many_sample(self, buf, number_of_samples_per_channel, timeout):
        buf[:] = self._fill_value


def test_read_loop_combines_per_device_blocks_into_the_right_columns():
    """The whole point of the multi-device read loop: each device's block lands in
    its own column range of one combined array, in acquisition.ai_channels order --
    no per-device timestamp reconciliation, just column-slice assembly (matching the
    proven reference implementation)."""
    import numpy as np

    from coherence.config import AcquisitionConfig

    backend = _backend()
    backend._acq = AcquisitionConfig(ai_channels=("Dev1/ai0", "Dev1/ai1", "Dev2/ai0"))
    backend._num_channels = 3
    backend._callback_chunk_size = 4
    backend._channels_by_device = {"Dev1": ["Dev1/ai0", "Dev1/ai1"], "Dev2": ["Dev2/ai0"]}
    backend._device_order = ["Dev1", "Dev2"]
    backend._readers = {"Dev1": _FakeReader(1.0), "Dev2": _FakeReader(2.0)}

    received = []

    def on_chunk(arr):
        received.append(arr.copy())
        backend._stop_event.set()

    backend._stop_event.clear()
    backend._read_loop(on_chunk)

    assert len(received) == 1
    combined = received[0]
    assert combined.shape == (4, 3)
    assert np.all(combined[:, 0:2] == 1.0)  # Dev1's two channels
    assert np.all(combined[:, 2:3] == 2.0)  # Dev2's one channel

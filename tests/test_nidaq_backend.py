"""Pre-flight validation tests for NIDaqBackend -- these catch the two
misconfigurations that actually caused a real "device cannot be accessed"
failure during hardware bring-up: a device name that doesn't match anything
currently connected, and a sample rate above what the connected device supports.

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


def test_rejects_device_name_not_currently_connected(connected_device):
    from coherence.daq.nidaq_backend import NIDaqBackend

    acq = AcquisitionConfig(device_name="ThisDeviceDoesNotExist", ai_channels=("ai0",))
    backend = NIDaqBackend(acq)
    with pytest.raises(RuntimeError, match="was not found"):
        backend._validate_against_detected_hardware()


def test_rejects_more_channels_than_the_device_has(connected_device):
    from coherence.daq.nidaq_backend import NIDaqBackend

    too_many = tuple(f"ai{i}" for i in range(len(connected_device.ai_channel_names) + 5))
    acq = AcquisitionConfig(device_name=connected_device.name, ai_channels=too_many)
    backend = NIDaqBackend(acq)
    with pytest.raises(RuntimeError, match="only has"):
        backend._validate_against_detected_hardware()


def test_rejects_sample_rate_above_device_max(connected_device):
    from coherence.daq.nidaq_backend import NIDaqBackend

    if connected_device.ai_max_multi_chan_rate_hz is None:
        pytest.skip("device doesn't report a max rate")
    acq = AcquisitionConfig(
        device_name=connected_device.name,
        ai_channels=("ai0",),
        sample_rate_hz=connected_device.ai_max_multi_chan_rate_hz * 4,
    )
    backend = NIDaqBackend(acq)
    with pytest.raises(RuntimeError, match="exceeds"):
        backend._validate_against_detected_hardware()


def test_accepts_a_config_that_matches_the_connected_device(connected_device):
    from coherence.daq.nidaq_backend import NIDaqBackend

    n = min(1, len(connected_device.ai_channel_names))
    acq = AcquisitionConfig(
        device_name=connected_device.name,
        ai_channels=tuple(f"ai{i}" for i in range(n)),
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

    acq = AcquisitionConfig(device_name="Dev1", ai_channels=("ai0",))
    backend = NIDaqBackend(acq)
    backend._error_queue.put(RuntimeError("simulated DAQmx read overrun"))

    backend.stop()  # must not raise


def test_drain_errors_returns_and_clears_queued_errors():
    from coherence.daq.nidaq_backend import NIDaqBackend

    acq = AcquisitionConfig(device_name="Dev1", ai_channels=("ai0",))
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

    acq = AcquisitionConfig(sample_rate_hz=51_200.0, device_name="Dev1", ai_channels=("ai0",))
    backend = NIDaqBackend(acq, callback_chunk_size=512)
    assert backend._driver_buffer_samples / acq.sample_rate_hz >= 3.0

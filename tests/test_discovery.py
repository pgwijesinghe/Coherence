"""Runs against whatever NI hardware is actually connected -- on the dev/test
machine this is a USB-4431 (Dev2). Skips gracefully if nidaqmx or a device
isn't present, since this suite must still pass on a machine with neither.
"""

import pytest

from coherence.daq import discovery

pytestmark = pytest.mark.skipif(
    not discovery.nidaqmx_available(), reason="nidaqmx not installed on this machine"
)


def test_list_devices_returns_something_or_empty_without_raising():
    devices = discovery.list_devices()
    assert isinstance(devices, list)


def test_find_device_matches_by_name():
    devices = discovery.list_devices()
    if not devices:
        pytest.skip("no NI device currently connected")
    first = devices[0]
    found = discovery.find_device(first.name)
    assert found is not None
    assert found.name == first.name


def test_find_device_returns_none_for_unknown_name():
    assert discovery.find_device("NotARealDeviceXYZ") is None

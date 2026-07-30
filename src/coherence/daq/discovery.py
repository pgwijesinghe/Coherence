"""NI hardware discovery.

Nothing here hardcodes a card model. Device names (`Dev1`, `Dev2`, `PXI1Slot2`, ...)
are assigned by NI-MAX / the driver and are not predictable or stable across
machines or reinstalls -- a config that hardcodes one is a common source of
"device cannot be accessed" errors on a machine other than the one it was written
on. Always discover what's actually plugged in and let the user pick from that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

try:
    import nidaqmx  # noqa: F401

    _NIDAQMX_AVAILABLE = True
except ImportError:
    _NIDAQMX_AVAILABLE = False


@dataclass(slots=True)
class DeviceSummary:
    name: str
    product_type: str
    is_simulated: bool
    ai_channel_names: list[str] = field(default_factory=list)
    ao_channel_names: list[str] = field(default_factory=list)
    ai_max_multi_chan_rate_hz: float | None = None
    ao_max_rate_hz: float | None = None
    ao_voltage_range: tuple[float, float] | None = None


def nidaqmx_available() -> bool:
    return _NIDAQMX_AVAILABLE


def _try(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - property support varies a lot between cards
        return default


def list_devices() -> list[DeviceSummary]:
    """Enumerate every NI device the driver currently sees. Returns [] if nidaqmx
    isn't installed or the driver can't be reached -- never raises, since this is
    used to populate UI pickers that should just show "nothing detected" instead
    of crashing the dialog.
    """
    if not _NIDAQMX_AVAILABLE:
        return []
    try:
        from nidaqmx.system import System

        system = System.local()
        devices = list(system.devices)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not reach NI-DAQmx to list devices: %s", exc)
        return []

    summaries = []
    for dev in devices:
        ao_ranges = _try(lambda: list(dev.ao_voltage_rngs), []) or []
        ao_range = None
        if len(ao_ranges) >= 2:
            pairs = [(float(ao_ranges[i]), float(ao_ranges[i + 1])) for i in range(0, len(ao_ranges) - 1, 2)]
            ao_range = max(pairs, key=lambda r: r[1] - r[0])

        summaries.append(
            DeviceSummary(
                name=dev.name,
                product_type=_try(lambda: dev.product_type, "") or "",
                is_simulated=bool(_try(lambda: dev.is_simulated, False)),
                ai_channel_names=[c.name for c in _try(lambda: list(dev.ai_physical_chans), []) or []],
                ao_channel_names=[c.name for c in _try(lambda: list(dev.ao_physical_chans), []) or []],
                ai_max_multi_chan_rate_hz=_try(lambda: float(dev.ai_max_multi_chan_rate)),
                ao_max_rate_hz=_try(lambda: float(dev.ao_max_rate)),
                ao_voltage_range=ao_range,
            )
        )
    return summaries


def find_device(name: str) -> DeviceSummary | None:
    for dev in list_devices():
        if dev.name == name:
            return dev
    return None


def first_ai_device(devices: list[DeviceSummary]) -> DeviceSummary | None:
    """The first device that can actually acquire. On PXI systems the chassis
    controller (and AO-only or timing modules) enumerate as devices too, so
    'the first device the driver lists' is not necessarily one with AI channels --
    picking blindly used to make a chassis full of 4461s look like no hardware."""
    return next((d for d in devices if d.ai_channel_names), None)


def driver_version() -> str | None:
    if not _NIDAQMX_AVAILABLE:
        return None
    try:
        from nidaqmx.system import System

        v = System.local().driver_version
        return f"{v.major_version}.{v.minor_version}.{v.update_version}"
    except Exception:  # noqa: BLE001
        return None

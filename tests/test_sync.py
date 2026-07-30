"""Tests for the shared multi-device DSA sync helpers (daq/sync.py), used by both
the AI acquisition backend and the AO stimulus generator. Ported from a sibling
project already verified on real multi-card PXIe-4461 chassis hardware --
see docs/hardware-notes.md.
"""

from coherence.daq import sync


class _FakeTiming:
    """Stands in for task.timing: plain attribute assignment, optionally rejecting
    specific values/properties to simulate a device that doesn't support them."""

    def __init__(self, rejects: set[str] | None = None, sync_time: float = 0.0, reject_sync_pulse: bool = False):
        # Bypass the rejection check for initial setup -- only assignments made by the
        # code under test (apply_reference_clock / apply_sync_pulse) should be rejected.
        super().__setattr__("_rejects", rejects or set())
        super().__setattr__("_reject_sync_pulse", reject_sync_pulse)
        super().__setattr__("sync_pulse_sync_time", sync_time)
        super().__setattr__("sync_pulse_min_delay_to_start", None)
        super().__setattr__("ref_clk_src", None)
        super().__setattr__("ref_clk_rate", None)
        super().__setattr__("sync_pulse_src", None)

    def __setattr__(self, name, value):
        if name == "ref_clk_src" and value in getattr(self, "_rejects", ()):
            raise RuntimeError(f"device rejects {value}")
        if name == "sync_pulse_src" and getattr(self, "_reject_sync_pulse", False):
            raise RuntimeError("device does not support sync pulse")
        super().__setattr__(name, value)


class _FakeTask:
    def __init__(self, rejects: set[str] | None = None, sync_time: float = 0.0, reject_sync_pulse: bool = False):
        self.timing = _FakeTiming(rejects=rejects, sync_time=sync_time, reject_sync_pulse=reject_sync_pulse)


def test_reference_clock_prefers_100mhz_when_requested_and_applies_to_every_task():
    tasks = {"Dev1": _FakeTask(), "Dev2": _FakeTask()}
    report = sync.apply_reference_clock(tasks, prefer_100mhz=True)

    assert all(t.timing.ref_clk_src == "PXIe_Clk100" for t in tasks.values())
    assert any("PXIe_Clk100" in line for line in report)


def test_reference_clock_falls_back_when_100mhz_rejected():
    # Dev2 can't do PXIe_Clk100 -- the whole group must fall back to PXI_Clk10 together,
    # not leave Dev1 on one clock and Dev2 on another.
    tasks = {"Dev1": _FakeTask(), "Dev2": _FakeTask(rejects={"PXIe_Clk100"})}
    sync.apply_reference_clock(tasks, prefer_100mhz=True)

    assert all(t.timing.ref_clk_src == "PXI_Clk10" for t in tasks.values())


def test_reference_clock_prefers_10mhz_first_when_not_pxie():
    tasks = {"Dev1": _FakeTask(), "Dev2": _FakeTask()}
    sync.apply_reference_clock(tasks, prefer_100mhz=False)

    assert all(t.timing.ref_clk_src == "PXI_Clk10" for t in tasks.values())


def test_reference_clock_degrades_gracefully_when_nothing_works():
    tasks = {
        "Dev1": _FakeTask(rejects={"PXIe_Clk100", "PXI_Clk10"}),
        "Dev2": _FakeTask(rejects={"PXIe_Clk100", "PXI_Clk10"}),
    }
    report = sync.apply_reference_clock(tasks, prefer_100mhz=True)  # must not raise

    assert any("none applied" in line for line in report)


def test_sync_pulse_routes_slaves_to_master_and_sets_worst_case_settle_delay():
    tasks = {
        "Dev1": _FakeTask(sync_time=0.002),  # master
        "Dev2": _FakeTask(sync_time=0.005),  # slave, slower settle time
    }
    report = sync.apply_sync_pulse(tasks, master_dev="Dev1")

    assert tasks["Dev2"].timing.sync_pulse_src == "/Dev1/SyncPulse"
    assert tasks["Dev1"].timing.sync_pulse_src is None  # master doesn't route to itself
    # worst-case delay applied to EVERY task, including the master
    assert tasks["Dev1"].timing.sync_pulse_min_delay_to_start == 0.005
    assert tasks["Dev2"].timing.sync_pulse_min_delay_to_start == 0.005
    assert any("Sync pulse" in line for line in report)
    assert any("settle delay" in line for line in report)


def test_sync_pulse_with_three_devices_only_routes_the_two_slaves():
    tasks = {"Dev1": _FakeTask(), "Dev2": _FakeTask(), "Dev3": _FakeTask()}
    sync.apply_sync_pulse(tasks, master_dev="Dev1")

    assert tasks["Dev1"].timing.sync_pulse_src is None
    assert tasks["Dev2"].timing.sync_pulse_src == "/Dev1/SyncPulse"
    assert tasks["Dev3"].timing.sync_pulse_src == "/Dev1/SyncPulse"


def test_sync_pulse_returns_empty_report_if_no_slave_accepts_it():
    tasks = {"Dev1": _FakeTask(), "Dev2": _FakeTask(reject_sync_pulse=True)}
    report = sync.apply_sync_pulse(tasks, master_dev="Dev1")

    assert report == []

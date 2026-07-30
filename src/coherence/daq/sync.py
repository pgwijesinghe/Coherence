"""Shared multi-device DSA synchronization helpers.

Both the AI acquisition backend and the AO stimulus generator need the exact same
reference-clock + sync-pulse dance across a set of per-device DAQmx tasks -- DSA
cards (4461/4462/4463, this project's own 4431) reject being combined into a single
multi-device task outright ("One or more devices do not support multidevice
tasks"), so both sides open one task per device and wire them together explicitly
instead. See docs/hardware-notes.md for the full rationale; this module exists so
that logic is written and tested exactly once.

Start-trigger routing is deliberately NOT here: it's a two-line call and the
terminal differs (`/<device>/ai/StartTrigger` vs `/<device>/ao/StartTrigger`), so
it stays inline in each caller rather than adding a parameter just to select a
string suffix.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def apply_reference_clock(tasks: dict, prefer_100mhz: bool) -> list[str]:
    """Locks every task's timebase to the same chassis clock so they don't
    free-run on independent oscillators and drift apart over a long run. Tried
    on every task including the master; failures are non-fatal since some
    buses/devices won't support a given source -- falls back to the next
    candidate, and to no shared clock at all (sync then relies on the start
    trigger alone) rather than raising."""
    report: list[str] = []
    candidates = (
        [("PXIe_Clk100", 100e6), ("PXI_Clk10", 10e6)]
        if prefer_100mhz
        else [("PXI_Clk10", 10e6), ("PXIe_Clk100", 100e6)]
    )
    for src, rate in candidates:
        ok, failed = 0, []
        for dev, task in tasks.items():
            try:
                task.timing.ref_clk_src = src
                task.timing.ref_clk_rate = rate
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{dev} ({exc.__class__.__name__})")
        if ok == len(tasks):
            report.append(f"Reference clock: {src} on all {ok} task(s)")
            return report
        logger.debug("Reference clock %s rejected by: %s", src, ", ".join(failed))
    report.append("Reference clock: none applied -- devices run on independent timebases and may drift.")
    logger.warning(
        "Could not apply a shared reference clock across %s -- devices may drift apart "
        "over a long run.", list(tasks)
    )
    return report


def apply_sync_pulse(tasks: dict, master_dev: str) -> list[str]:
    """Aligns delta-sigma converter filter state across DSA cards. A shared
    reference clock alone still leaves two DSA cards' internal decimation filters
    a sample or more apart; the sync pulse is what actually aligns sample 0."""
    report: list[str] = []
    slaves = {dev: t for dev, t in tasks.items() if dev != master_dev}
    applied = 0
    for dev, task in slaves.items():
        try:
            task.timing.sync_pulse_src = f"/{master_dev}/SyncPulse"
            applied += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: sync pulse routing failed: %s", dev, exc)
    if not applied:
        return report
    report.append(f"Sync pulse /{master_dev}/SyncPulse -> {applied} slave task(s)")

    # Nobody may start until every card's filter has settled -- take the worst case
    # across all participating devices, not just the slaves.
    sync_times = []
    for task in tasks.values():
        try:
            sync_times.append(float(task.timing.sync_pulse_sync_time))
        except Exception:  # noqa: BLE001
            pass
    if not sync_times:
        return report
    worst = max(sync_times)
    for task in tasks.values():
        try:
            task.timing.sync_pulse_min_delay_to_start = worst
        except Exception:  # noqa: BLE001
            pass
    report.append(f"Sync settle delay: {worst * 1e3:.3f} ms")
    return report

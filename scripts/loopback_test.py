"""End-to-end hardware loopback test: generates a known two-tone stimulus on AO0,
reads it back on AI0 (wire AO0 -> AI0 before running), and demodulates both tones
through the real production FFT lock-in pipeline (NIDaqBackend + LockinPipeline +
FFTLockinEngine, unmodified). This is exactly the FDM lock-in scenario the whole
project targets: two frequency-multiplexed channels sharing one physical input.

Success looks like:
  - recovered amplitude for each channel close to its injected amplitude
    (loopback insertion loss should be ~0 on a direct wire)
  - phase roughly CONSTANT block-to-block (not necessarily zero -- AO and AI run
    on independent free-running tasks on this device, so there's an arbitrary but
    fixed relative phase offset; what matters is that it isn't drifting/random,
    which would indicate a real synchronization or continuity problem)

    python scripts/loopback_test.py
"""

from __future__ import annotations

import logging
import time

import numpy as np

from coherence.config import AcquisitionConfig, ChannelConfig, LockinConfig
from coherence.core.pipeline import LockinPipeline
from coherence.daq import discovery
from coherence.daq.ao_stimulus import AOChannelSpec, AOStimulusGenerator, ToneSpec
from coherence.daq.nidaq_backend import NIDaqBackend

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

SAMPLE_RATE = 51_200.0
BLOCK_SIZE = 2048
BIN_SPACING = SAMPLE_RATE / BLOCK_SIZE  # 25 Hz

STIMULUS = [
    ToneSpec(frequency_hz=25 * BIN_SPACING, amplitude_v=1.0),   # bin 25 = 625 Hz, coherent
    ToneSpec(frequency_hz=48 * BIN_SPACING, amplitude_v=0.5),   # bin 48 = 1200 Hz, coherent
]
RUN_SECONDS = 3.0


def main() -> None:
    devices = discovery.list_devices()
    if not devices:
        raise SystemExit("No NI devices detected -- connect the card and try again.")
    dev = devices[0]
    if not dev.ao_channel_names:
        raise SystemExit(
            f"{dev.name} ({dev.product_type}) has no AO channel -- this loopback test needs "
            "one (a USB-4431 has ao0 built in; a PXIe-4431 does not)."
        )

    print(f"Using {dev.name} ({dev.product_type}): AO {dev.ao_channel_names[0]}, "
          f"AI range {dev.ai_channel_names[:1]}, AO range {dev.ao_voltage_range}")
    print(f"Sample rate {SAMPLE_RATE:.0f} Hz, block {BLOCK_SIZE} samples, bin spacing {BIN_SPACING:.1f} Hz")
    print("Stimulus: " + ", ".join(f"{t.frequency_hz:.0f} Hz @ {t.amplitude_v:.2f} V" for t in STIMULUS))

    ai_channel = dev.ai_channel_names[0]
    acq = AcquisitionConfig(
        sample_rate_hz=SAMPLE_RATE,
        block_size=BLOCK_SIZE,
        overlap_fraction=0.5,
        window="blackmanharris",
        ai_channels=(ai_channel,),
        input_range_v=10.0,
    )
    channels = [
        ChannelConfig(name=f"CH{i+1}", frequency_hz=t.frequency_hz, input_channel=0)
        for i, t in enumerate(STIMULUS)
    ]
    config = LockinConfig(acquisition=acq, channels=channels)
    for ch in channels:
        err = config.coherence_error_hz(ch)
        assert err < 1e-6, f"{ch.name} is {err:.3f} Hz off-bin -- fix STIMULUS frequencies"

    stimulus = AOStimulusGenerator(
        sample_rate_hz=SAMPLE_RATE,
        buffer_size=BLOCK_SIZE,
        channels=[
            AOChannelSpec(
                ao_channel=dev.ao_channel_names[0],
                tones=STIMULUS,
                voltage_range=dev.ao_voltage_range or (-3.5, 3.5),
            )
        ],
    )

    results = []
    print(f"\nStarting AO stimulus and AI acquisition, running {RUN_SECONDS:.0f}s ...")
    with stimulus:
        backend = NIDaqBackend(acq)
        pipeline = LockinPipeline(config, backend)
        pipeline.add_result_callback(results.append)
        pipeline.start()
        time.sleep(RUN_SECONDS)
        pipeline.stop()

    print(f"\nblocks_processed={pipeline.stats.blocks_processed} overruns={pipeline.stats.overruns} "
          f"measured_update_rate_hz={pipeline.stats.measured_update_rate_hz:.1f}")

    if len(results) < 5:
        raise SystemExit("Too few blocks captured to evaluate -- check wiring and try again.")

    settle = len(results) // 4  # drop the first quarter: filter/settling transient
    steady = results[settle:]

    print(f"\n{'Channel':8s} {'Freq (Hz)':>10s} {'Injected (V)':>13s} {'Recovered (V)':>14s} "
          f"{'Phase mean (deg)':>17s} {'Phase std (deg)':>16s}")
    ok = True
    for ch, tone in zip(channels, STIMULUS):
        amps = np.array([r.channels[ch.name].amplitude for r in steady])
        phases_deg = np.degrees(np.array([r.channels[ch.name].phase_rad for r in steady]))
        amp_mean = amps.mean()
        phase_mean = np.degrees(np.angle(np.mean(np.exp(1j * np.radians(phases_deg)))))
        phase_std = np.degrees(np.std(np.unwrap(np.radians(phases_deg))))
        print(f"{ch.name:8s} {tone.frequency_hz:10.1f} {tone.amplitude_v:13.3f} {amp_mean:14.4f} "
              f"{phase_mean:17.2f} {phase_std:16.3f}")

        amp_ratio = amp_mean / tone.amplitude_v
        if not (0.85 < amp_ratio < 1.15):
            print(f"  WARNING: recovered/injected amplitude ratio {amp_ratio:.2f} is outside "
                  f"[0.85, 1.15] -- check the AO0->AI0 wiring and AI input range/coupling.")
            ok = False
        if phase_std > 5.0:
            print(f"  WARNING: phase std {phase_std:.2f} deg is high for a steady tone -- "
                  f"check for dropped samples (overruns={pipeline.stats.overruns}) or a loose connection.")
            ok = False

    if pipeline.stats.overruns > 0:
        print(f"\nWARNING: {pipeline.stats.overruns} ring-buffer overrun(s) during the run.")
        ok = False

    print("\nLOOPBACK TEST " + ("PASSED" if ok else "FAILED"))


if __name__ == "__main__":
    main()

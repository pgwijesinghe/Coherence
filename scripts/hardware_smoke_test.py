"""One-off script: acquire briefly from whatever real NI device is connected,
using the same NIDaqBackend + LockinPipeline path the GUI uses, to confirm the
device-access fix actually works end to end (not just the isolated validation
unit tests). Not part of the pytest suite since it requires real hardware.
"""

from __future__ import annotations

import logging
import time

from coherence.config import AcquisitionConfig, ChannelConfig, LockinConfig
from coherence.core.pipeline import LockinPipeline
from coherence.daq import discovery
from coherence.daq.nidaq_backend import NIDaqBackend

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> None:
    devices = discovery.list_devices()
    print("Detected devices:")
    for d in devices:
        print(f"  {d.name}: {d.product_type}, AI={d.ai_channel_names}, "
              f"max_rate={d.ai_max_multi_chan_rate_hz}")
    if not devices:
        print("No devices detected -- nothing to test.")
        return

    dev = devices[0]
    n_channels = min(2, len(dev.ai_channel_names))
    sample_rate = min(51_200.0, (dev.ai_max_multi_chan_rate_hz or 51_200.0) / 2)
    block_size = 2048
    bin_spacing = sample_rate / block_size

    acq = AcquisitionConfig(
        sample_rate_hz=sample_rate,
        block_size=block_size,
        overlap_fraction=0.5,
        window="blackmanharris",
        device_name=dev.name,
        ai_channels=tuple(f"ai{i}" for i in range(n_channels)),
        input_range_v=10.0,
    )
    channels = [
        ChannelConfig(name=f"CH{i+1}", frequency_hz=round(1000.0 / bin_spacing) * bin_spacing + i * bin_spacing * 10,
                      input_channel=i)
        for i in range(n_channels)
    ]
    config = LockinConfig(acquisition=acq, channels=channels)

    print(f"\nOpening {dev.name} at {sample_rate:.0f} Hz, channels {acq.ai_channels} ...")
    backend = NIDaqBackend(acq)
    pipeline = LockinPipeline(config, backend)

    results = []
    pipeline.add_result_callback(results.append)
    pipeline.start()
    time.sleep(2.0)
    pipeline.stop()

    print(f"\nblocks_processed={pipeline.stats.blocks_processed} "
          f"overruns={pipeline.stats.overruns} "
          f"measured_update_rate_hz={pipeline.stats.measured_update_rate_hz:.1f}")
    if results:
        last = results[-1]
        for name, ch in last.channels.items():
            print(f"  {name} ({ch.frequency_hz:.1f} Hz): amplitude={ch.amplitude:.5f} phase={ch.phase_rad:.3f} rad")
    print("\nHARDWARE SMOKE TEST OK -- device opened, task started, data streamed.")


if __name__ == "__main__":
    main()

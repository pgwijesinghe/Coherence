# Hardware notes

Things learned while bringing Coherence up on a real card (a USB-4431), written down
so the next person doesn't have to rediscover them the hard way. Most of these are
general NI-DAQmx facts rather than anything specific to this project.

## Nothing detected? Start here

Run the diagnostic before anything else:

```
uv run coherence --list
```

It prints the driver version and every device the driver can see, and tells you
which of the usual suspects you're dealing with:

1. *"nidaqmx is not installed"* — the environment was set up without the hardware
   extra. Fix with `uv pip install -e ".[hardware]"`. This is easy to hit on a
   freshly cloned machine, because the base install deliberately doesn't depend on
   NI software.
2. *Driver present, no devices* — the driver can't see the hardware. On PXI systems
   the classic cause is power order: the chassis must be on before the host boots,
   or the modules never enumerate. Confirm the cards show up in NI MAX under
   Devices and Interfaces (with no warning icons) before expecting anything from
   this app; if MAX can't see them, no user software can.
3. *Devices listed but "none has AI channels"* — you're seeing chassis controllers
   or output-only modules. The app skips those automatically when picking a device
   at startup, and the Hardware tab shows everything so you can tell what's what.

## Multiple cards in one chassis

Every AI channel across every detected device is combined into one flat channel
list, and every AO channel likewise — a chassis with six 4461s shows up as one
roster of 12 AI + 12 AO channels, not six separate single-card configurations.
Concretely: `AcquisitionConfig.ai_channels` and `AOChannelConfig.ao_channel` hold
full physical paths (`"PXI1Slot3/ai0"`), not a bare channel name plus one shared
device name — that's what lets channels from several cards sit side by side in
one config, and what `effective_ai_config` and the FFT engine key off unchanged.

**Correction, found on real multi-card hardware:** an earlier version of this
project assumed DAQmx would transparently combine AI channels from several
devices into one task and synchronize them itself. That's wrong for DSA cards.
Putting a 4461's channels in the same task as a second 4461's channels fails
outright with *"One or more devices do not support multidevice tasks."* DSA
cards (4461/4462/4463, this project's own 4431) can't be combined into a
multi-device task at all — full stop, not a degraded/unsynchronized fallback,
a hard error.

The actual fix, ported from a sibling project already verified on real
multi-card PXIe-4461 chassis hardware: **one DAQmx task per device**, kept
sample-aligned by explicitly wiring three things across the per-device tasks,
matching the classic DAQmx multi-card synchronization recipe:

1. **Reference clock.** Every task's timebase locks to the same chassis clock
   (`task.timing.ref_clk_src = "PXIe_Clk100"`, falling back to `"PXI_Clk10"` if
   rejected) — applied to *every* task, master included. Without this each
   card's onboard oscillator free-runs independently and they drift apart over
   a long acquisition.
2. **Sync pulse.** A shared clock alone still isn't enough for DSA cards
   specifically: their delta-sigma converters carry internal decimation filter
   state that a clock and trigger don't reset, so two cards can land a sample
   or more apart even with perfect clock sync. Every slave task's
   `timing.sync_pulse_src` points at `/<master_device>/SyncPulse`; every task
   (master included) then gets `timing.sync_pulse_min_delay_to_start` set to
   the *worst-case* `sync_pulse_sync_time` reported across all participating
   devices, so nothing starts before the slowest card has settled.
3. **Start trigger.** Every slave task's start trigger is configured to
   `/<master_device>/ai/StartTrigger`. Calling `.start()` on a task with a
   digital-edge start trigger configured just arms it and returns — it doesn't
   actually begin until the edge arrives. Slave tasks are started first (armed,
   waiting), the master task is started *last*, and that's the edge that
   releases every armed slave at the same sample.

Reading back is a single background thread that, once per chunk, calls
`read_many_sample` on each device's own `AnalogMultiChannelReader` in turn and
writes each device's block into its column range of one shared array — no
per-device timestamp reconciliation, because steps 1–3 above are what
guarantee sample index *n* means the same instant on every device. If ref
clock or sync pulse silently failed to apply to some card (both degrade
gracefully rather than raising), that concatenation is no longer trustworthy —
`NIDaqBackend.sync_report` logs exactly what was and wasn't applied, at INFO
level, so a synchronization problem shows up in the log instead of just
producing quietly-wrong data.

Master selection: the first device in `acquisition.devices` (i.e. the device
of the first channel in `acquisition.ai_channels`) — there's no override for
this yet, unlike the single-device path's `clock_source`/`start_trigger_source`
parameters.

**This applies to AO generation too, and was initially missed.** The first fix
only touched the AI acquisition backend; `AOStimulusGenerator` still put every
enabled reference-output channel into one task regardless of device, which
hits the exact same "multidevice tasks" error the moment two reference
channels live on different cards. It needed the identical one-task-per-device
treatment — grouped by device, reference clock and sync pulse applied when
more than one device is involved, slaves-first/master-last start order, each
device's own task written with its own waveform. The reference-clock and
sync-pulse logic itself lives in `daq/sync.py` and is shared verbatim between
the AI backend and the AO generator rather than duplicated, specifically so a
fix or a subtlety only has to be gotten right once.

The sample rate for a multi-device acquisition is bounded by whichever
participating device is slowest — `_validate_against_detected_hardware` checks
against the minimum across all of them and names the limiting device if the
configured rate is too high.

This sync sequence is a direct, careful port of a proven implementation (see
that project's `sessions.py`) rather than something re-derived from scratch,
specifically because a wrong guess here produces data that *looks* fine —
every channel reads a plausible-looking amplitude and phase — while actually
being misaligned by an unknown number of samples between cards. The single-
device path (one card, no cross-task sync needed) is untouched by any of this
and remains the same code verified extensively via loopback tests.

The Hardware tab's device table supports selecting more than one row (Ctrl/Shift-
click) — "Use Selected" combines just those cards, "Use All Detected" combines
everything the driver currently sees.

Also worth knowing: the `nidaqmx` Python package requires a reasonably recent
DAQmx driver. On an older lab machine that has only ever run LabVIEW, the driver
may predate what the package supports — `coherence --list` failing with a driver
error rather than listing devices is the symptom, and updating NI-DAQmx (the
driver, not the Python package) is the fix.

## Device names are not portable

`Dev1`, `Dev2`, `PXI1Slot2` and friends are assigned by NI-MAX per machine, and they
survive neither a different computer nor, sometimes, a driver reinstall. A config
that hardcodes a device name works exactly until it meets a second machine, at which
point the driver reports the unhelpful "device cannot be accessed". This bit us on
day one: the code assumed `Dev1`, the card enumerated as `Dev2`.

The rule since then: never trust a stored device name. The app enumerates connected
devices at startup and whenever asked (Hardware tab, Rescan), configuration is built
from what is actually present, and a pre-flight check compares the configured device,
channel count, and sample rate against the live hardware before a task is ever
created — so a mismatch produces a message naming the problem instead of a raw DAQmx
status code.

## Buffer sizes: two separate traps

**Trap one, at startup.** DAQmx auto-sizes its input buffer from the requested sample
rate, and on some devices the size it picks is not a multiple of the every-N-samples
callback interval. The task then refuses to start with error −200920 ("Every N
Samples Event Interval is not supported for the given buffer size"). The USB-4431
does exactly this. The fix is to compute a buffer size that is an exact multiple of
the callback interval and force it with `in_stream.input_buf_size` instead of
trusting the auto-sizing.

**Trap two, while running.** Python's GIL means the DAQmx callback thread shares one
interpreter with the FFT worker and the entire Qt GUI. Any stretch where the callback
can't run — a garbage collection pause, a heavy repaint — eats into the driver
buffer, and if the buffer is small the task dies with −200279 ("the application is
not able to keep up with the hardware acquisition"). The original buffer held about
0.6 s of data, which was not enough. It now defaults to 5 seconds' worth. Transient
read errors are also treated as recoverable — logged, counted in the overrun counter,
acquisition continues with a gap — rather than fatal, and a stored error is never
re-raised from `stop()`, because an earlier version did exactly that and broke window
close on top of the original failure.

## The GUI can starve the acquisition

The single largest source of overruns turned out to be nothing in the acquisition
path at all: it was plot rendering. Repainting antialiased, wide-stroke curves with
thousands of points, thirty times a second, for every plot widget including the ones
on hidden tabs, kept the GUI thread busy enough that the callback thread lost the GIL
race. Symptoms were a continuous stream of −200279 errors whenever the amplitude or
spectrum tab was open, and none otherwise.

What fixed it, in order of impact:

1. Only the visible tab is painted. Hidden widgets do zero work per frame.
2. Antialiasing off and pen width 1 — width-1 cosmetic pens keep Qt on its fast
   line-drawing path, wider pens quietly fall off it.
3. pyqtgraph's automatic downsampling and clip-to-view on the strip charts, so the
   painted point count stays bounded regardless of history length.
4. The full diagnostic spectrum is computed at ~10 Hz instead of at the demodulation
   rate. Nobody can watch a spectrum refresh at 50 Hz anyway; the demodulated
   amplitude and phase still update on every block.
5. The acquisition only reads the physical channels that enabled demodulation
   channels actually reference. Reading all four inputs of a card to use one of them
   is pure per-callback overhead. Enabling a channel needs a restart regardless
   (the FFT engine's channel layout is fixed when built), so nothing is lost.

After all five, the app runs every channel of a 4431 with plot tabs cycling and zero
overruns. The lesson generalises: in a Python acquisition app, treat GUI-thread work
as if it were inside the acquisition loop, because through the GIL it effectively is.

## Frequencies the hardware can't measure

Two validation checks exist because each caught a real mistake:

- A demodulation frequency above Nyquist (`fs/2`) computes an FFT bin index past the
  end of the spectrum array. Instead of the cryptic "index 520 is out of bounds for
  axis 0 with size 513" this used to produce, the engine now refuses at construction
  with the actual limit and the offending channel named.
- The coherence check in the status bar considers only *enabled* channels. Disabled
  channels keep whatever frequency they were configured with, and after a sample-rate
  change those stale values are usually off-bin — which triggered warnings about
  measurements that weren't even running.

## Quirks of stimulus generation

The reference outputs are generated by writing one buffer of exactly `N` samples (the
FFT block size) and letting the card regenerate it forever. This is glitch-free only
if every tone completes a whole number of cycles in the buffer — the same coherent
condition the acquisition side needs anyway, which is a happy coincidence: a
frequency valid for demodulation is automatically valid for generation. The generator
refuses frequencies that don't satisfy it, and also refuses amplitude combinations
whose peak exceeds the card's output range (a USB-4431 clips at ±3.5 V, not the
±10 V you might assume).

Frequency and amplitude edits while running are applied by tearing down and
rebuilding only the AO task; acquisition is untouched. Note that on a free-running
setup (no shared start trigger between the AO and AI tasks) the absolute phase
between stimulus and demodulator is arbitrary — constant within a run, different
between runs. For relative measurements this is irrelevant; for absolute phase, share
a start trigger.

## Sample clock reality

Ask DAQmx for 20 000 S/s and you may get 20 000.00048 S/s — the driver coerces the
request to the nearest rate its clock dividers can synthesise. At parts-per-billion
this is irrelevant on any timescale we care about, but it is worth knowing the
coerced value exists if you ever compare timestamps against an external clock over
hours.

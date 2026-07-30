# Coherence

A software lock-in amplifier for NI data acquisition hardware, built around a single
FFT instead of a bank of per-channel demodulators.

The usual way to build a multichannel digital lock-in (including our LabVIEW-based
predecessor) is to give every channel its own reference oscillator, mixer, and low-pass
filter. That works, but the cost grows linearly with channel count and most of the
computation is redundant. Coherence takes a different route: modulate each channel at a
nearby but distinct frequency (say 50, 51, 52 kHz on one input line), take one windowed
FFT per block of samples, and read each channel's amplitude and phase directly off its
FFT bin. One transform serves every channel on that input, and adding another channel
costs essentially nothing.

The catch is that this only works if you respect one condition: every frequency of
interest must land exactly on an FFT bin. The whole application is organised around
making that condition easy to satisfy and loudly visible when it isn't. Hence the name.

A short derivation of why an FFT bin is exactly equivalent to a conventional IQ
demodulator, and what the window function has to do with the classic lock-in "time
constant", is in [docs/theory.md](docs/theory.md). Practical lessons from bringing the
software up on real hardware are in [docs/hardware-notes.md](docs/hardware-notes.md).

## What it does

- Continuous streaming acquisition from NI-DAQmx devices (developed against a
  USB-4431; also aimed at the PXIe-4461/4462 family). Nothing about a specific card is
  hardcoded: device names, channel counts, and rate limits are read from the driver.
- Any number of demodulation channels per physical input. Each is defined by a name,
  a frequency, and the input it listens on.
- Reference signal generation on the card's analog outputs, so a self-contained
  stimulus-and-measure experiment needs no external function generator. Output
  frequency and amplitude can be changed live while a run is in progress.
- A Qt front panel with live amplitude/phase strip charts, a spectrum view with
  markers at each demodulation frequency, a phasor (X-Y) display, and a numeric
  read-out table.
- HDF5 logging of amplitude, phase, X, and Y per channel with acquisition metadata.
- A simulation backend that generates multitone test signals at wall-clock pace, so
  the full application runs on a machine with no NI hardware or drivers installed.

## Installation

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/) (plain pip works too).

```
git clone https://github.com/pgwijesinghe/Coherence.git
cd Coherence
uv venv
uv pip install -e ".[dev]"
```

To talk to real hardware you also need the NI-DAQmx driver installed, plus the Python
bindings:

```
uv pip install -e ".[hardware]"
```

Then run:

```
uv run coherence
```

On startup the app enumerates connected NI devices and builds a working channel roster
from the first one it finds: one row per physical AI channel, one output entry per AO
channel, sample rate clamped to what the card can actually do. With no hardware
present it falls back to the simulator. The Hardware tab lists everything the driver
can see and lets you rescan or switch devices.

## Choosing your numbers

Three parameters interact: the sample rate `fs`, the FFT block size `N`, and your
modulation frequencies. The rules:

1. **Coherence.** Each frequency must be an integer multiple of the bin spacing
   `fs / N`. For example at `fs = 15360` and `N = 1024` the bin spacing is 15 Hz, so
   15, 30, 45 Hz... are all valid; 20 Hz is not. The status bar shows a green
   "Coherent" check when every enabled channel satisfies this, and names the
   offending channel when one doesn't. Off-bin frequencies suffer amplitude error
   (scalloping) and phase error, which defeats the point of a lock-in.

2. **Nyquist, with margin.** Frequencies must be below `fs / 2`, and comfortably so.
   The app refuses to start if a channel is above Nyquist rather than silently
   aliasing.

3. **Stay away from DC.** The window function spreads each spectral component over a
   few bins (four on each side for the default Blackman-Harris window), and bin 0 is
   the signal's DC offset. A tone on bin 1 or 2 sits inside that spread and any DC
   offset in the signal will leak into your reading. Keep tones at bin 8 or higher;
   if your modulation frequency is fixed and low, grow `N` until it lands on a higher
   bin. As a bonus, a larger `N` integrates longer per block, which is exactly the
   lock-in time-constant trade-off: better noise rejection, slower response.

The configuration dialog computes bin spacing, update rate, and block duration live as
you change values, so you can sanity-check a setup before starting it.

## Layout

```
src/coherence/
  config.py                  channel and acquisition settings, coherence checks
  dsp/
    windows.py               window functions with gain/bandwidth corrections
    fft_engine.py            block in, per-channel amplitude/phase/X/Y out
  daq/
    discovery.py             what hardware does the driver see
    autoconfig.py            turn a detected device into a runnable config
    nidaq_backend.py         NI-DAQmx continuous acquisition
    ao_stimulus.py           multitone reference generation on AO
    simulated_backend.py     hardware-free multitone source
  core/
    ring_buffer.py           acquisition thread -> FFT worker handoff
    pipeline.py              ties backend, buffer, engine and consumers together
  logging/
    hdf5_logger.py           per-channel HDF5 output
  ui/                        Qt front panel (PySide6 + pyqtgraph)
scripts/                     hardware verification scripts, see below
tests/                       pytest suite, runs without hardware
```

The acquisition backend, ring buffer, and FFT engine know nothing about Qt, so the
whole measurement path can be scripted without the GUI — the files in `scripts/` do
exactly that.

## Verifying against real hardware

The test suite (`uv run pytest`) covers the DSP and application logic and runs
anywhere. The scripts under `scripts/` go further and exercise a physically connected
card; the most useful one is the loopback test:

```
uv run python scripts/loopback_test.py
```

Wire AO0 to AI0, and the script generates a two-tone stimulus, demodulates both tones
through the production pipeline, and checks the recovered amplitudes and phase
stability against what was injected. On a USB-4431 with a direct wire it recovers
amplitude to within about 0.15% with phase flat to well under a degree, which is a
good end-to-end check that acquisition, timing, and the demodulation math all agree.

## Status

Working and verified on a USB-4431. Multi-card synchronisation (shared reference
clock and start trigger across a PXIe chassis) is designed for but not yet exercised
on a real chassis — the hooks are in `nidaq_backend.py` (`clock_source`,
`start_trigger_source`). If AO and AI must be phase-locked with no arbitrary offset,
both tasks need to share a start trigger; on a free-running setup the relative phase
is constant within a run but arbitrary between runs.

## License

No license file yet — ask before reusing.

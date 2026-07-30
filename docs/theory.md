# Why an FFT bin is a lock-in amplifier

This note works through the equivalence that the whole application rests on. None of
it is new — it's standard DSP — but the pieces are usually presented separately, and
the connection between "FFT bin" and "lock-in output" deserves to be spelled out once,
carefully.

## The conventional lock-in

A lock-in amplifier recovers the amplitude and phase of a signal buried in noise by
exploiting one piece of prior knowledge: the signal's exact frequency. The classic
digital implementation multiplies the input `x[n]` by a quadrature reference at that
frequency and low-pass filters the two products:

    X[n] = LPF{ x[n] · cos(2π f n / fs) }
    Y[n] = LPF{ x[n] · sin(2π f n / fs) }

    R = 2·sqrt(X² + Y²)        amplitude
    θ = atan2(Y, X)            phase

The mixing shifts the component at `f` down to DC; the low-pass filter then rejects
everything else. The filter's bandwidth — set by the familiar "time constant" knob on
a bench lock-in — decides how much noise gets through and how fast the output can
respond. Narrow filter, clean but slow; wide filter, fast but noisy.

Equivalently and more compactly: multiply by the complex reference `exp(-j2πfn/fs)`
and low-pass the complex product. Keep that formulation in mind.

## The DFT bin

Now write down what one bin of a windowed DFT computes:

    X[k] = Σ  w[n] · x[n] · exp(-j 2π k n / N)      for n = 0 … N-1

This is a correlation of the input against a complex exponential at frequency
`f_k = k · fs / N`, weighted by the window `w[n]`. Compare it with the complex lock-in
above: it is the same operation. The multiplication by `exp(-j…)` is the mixer; the
sum over a finite window is a low-pass filter — specifically an FIR filter whose
impulse response *is* the window function, evaluated once per block.

So each DFT bin is a complete lock-in channel:

- the bin index `k` picks the reference frequency,
- the window shape is the low-pass filter,
- the block length `N/fs` plays the role of the time constant,
- `|X[k]|` and `arg(X[k])` are R and θ (after the corrections below).

The FFT computes all `N/2` such channels simultaneously in `O(N log N)`. That is the
entire trick: with channels frequency-multiplexed onto one input, one transform per
block replaces one mixer-plus-filter chain per channel, and reading out extra
channels is free. A per-channel implementation costs `O(N)` per channel per block;
here the cost is flat regardless of how many bins you use.

## The condition that makes it exact

The equivalence is exact only when the signal frequency sits exactly on a bin:

    f · N / fs  =  integer

This is the *coherent sampling* condition — the tone completes a whole number of
cycles within one block. When it holds, the correlation is perfectly matched and the
bin returns the true amplitude and phase. When it doesn't, the tone's energy smears
across neighbouring bins (spectral leakage), the peak bin under-reads (scalloping
loss, up to ~3.9 dB for a rectangular window at the half-bin worst case), and the
phase estimate is biased. There is no post-hoc correction as clean as simply choosing
`fs`, `N`, and `f` so the condition holds, which is why the application checks it
continuously and puts the result in the status bar.

One subtlety: it isn't enough for the numbers to be right on paper. The generator
producing the tone and the ADC sampling it must share a clock, otherwise their
independent oscillators drift apart and a tone that started on-bin slowly walks off
it. When Coherence generates the stimulus itself on the same card that acquires, this
is automatic. Across separate instruments, lock them to a common 10 MHz reference.

## Windows, gain, and channel separation

The window trades three things against each other:

| window          | main lobe width | highest sidelobe | amplitude flatness |
|-----------------|-----------------|------------------|--------------------|
| rectangular     | narrowest       | −13 dB           | poor off-bin       |
| Hann            | 2× rectangular  | −32 dB           | moderate           |
| Blackman-Harris | 4× rectangular  | −92 dB           | good enough        |
| flat-top        | ~10×            | −90 dB           | excellent          |

For closely spaced multiplexed channels the sidelobe level sets how much one channel
bleeds into its neighbours, which is why Blackman-Harris is the default: −92 dB of
rejection with a main lobe that still fits comfortably when channels are spaced ten
or more bins apart. Two consequences to keep in mind:

1. The window attenuates the signal by its average value (the *coherent gain*), so
   the raw bin magnitude must be divided by `N · CG` — and multiplied by 2 to account
   for the energy in the negative-frequency half — to read out true volts. The code
   does this; the numbers in `dsp/windows.py` are the standard ones from Harris'
   1978 paper.

2. The main lobe applies around *every* spectral component, including DC. A tone
   parked on bin 1 or 2 sits inside the skirt of whatever DC offset the signal
   carries, and the offset leaks into the measurement. Keep working tones at bin 8
   or above with the default window.

## Phase needs a fixed origin

The raw phase of a bin is referenced to the start of the current block. Blocks start
at different absolute times as acquisition rolls on, so raw phase precesses from
block to block even for a perfectly stable signal — at anything except full-block
hops it would spin continuously. The fix is bookkeeping, not filtering: track the
absolute sample index at which each block starts and subtract the phase the reference
would have accumulated by then,

    θ = arg(X[k]) − 2π f · n_start / fs      (wrapped to ±π)

After this correction the reported phase is steady, and its block-to-block standard
deviation is a direct health check on the acquisition — in loopback on real hardware
it sits below a millidegree.

## Latency, update rate, and overlap

One block of `N` samples takes `N/fs` seconds to fill, which sets both the intrinsic
latency and, with disjoint blocks, the output update rate. Those can be decoupled:
sliding the analysis window by less than a full block (overlap processing) raises the
update rate without touching the noise bandwidth, at proportional compute cost. The
default is 50% overlap. The equivalent noise bandwidth itself is set by the window
and block length — `ENBW = enbw_bins · fs / N`, with `enbw_bins` = 2.0 for
Blackman-Harris — which is the number to use when converting noise floors to
per-root-hertz figures.

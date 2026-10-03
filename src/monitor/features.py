"""Condition features of one accelerometer channel in one snapshot.

Time-domain statistics say how strongly and how impulsively a bearing vibrates. Envelope
amplitudes at the fault frequencies say which part the impacts come from.
"""

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy import signal, stats

from monitor.ims import FAULT_FREQUENCIES, SAMPLE_RATE_HZ, FaultFrequencies

Signal = npt.NDArray[np.float64]

# Where the housing rings after an impact. The resonance hump already shows in healthy
# snapshots, so the band is chosen without looking at failures.
ENVELOPE_BAND_HZ = (2_000.0, 8_000.0)
# Rollers slip under load, which shifts each harmonic by the same percentage. Kept below 1.3 %
# so the band around BPFO stays clear of the 7th shaft harmonic (233.3 Hz).
FAULT_BAND_RELATIVE = 0.01
# One FFT bin either side, so even the low cage harmonics get a band.
FAULT_BAND_MIN_HZ = 1.0
# Harmonics grow with damage, and a roller defect often shows only at 2 x BSF.
HARMONICS = 3


@dataclass(frozen=True)
class ChannelFeatures:
    rms: float
    peak: float
    crest_factor: float
    kurtosis: float
    # Envelope amplitude in V, summed over the first HARMONICS multiples of each fault frequency.
    env_ftf: float
    env_bsf: float
    env_bpfo: float
    env_bpfi: float


def envelope_spectrum(x: Signal, sample_rate: float) -> tuple[Signal, Signal]:
    """Frequencies and amplitudes of the envelope of x's resonance band."""
    # Second-order sections stay numerically stable where the plain (b, a) form of a narrow
    # high-order band-pass does not.
    sos = signal.butter(4, ENVELOPE_BAND_HZ, btype="bandpass", fs=sample_rate, output="sos")
    envelope = np.abs(signal.hilbert(signal.sosfiltfilt(sos, x)))
    # The envelope is always positive; its mean would show up as a huge peak at 0 Hz.
    envelope = envelope - envelope.mean()
    freqs = np.fft.rfftfreq(len(x), 1 / sample_rate)
    amplitudes = np.abs(np.fft.rfft(envelope)) * 2 / len(x)
    return freqs, amplitudes


def harmonic_amplitude(freqs: Signal, amplitudes: Signal, base_hz: float) -> float:
    total = 0.0
    for h in range(1, HARMONICS + 1):
        center = h * base_hz
        half_width = max(FAULT_BAND_RELATIVE * center, FAULT_BAND_MIN_HZ)
        total += float(amplitudes[np.abs(freqs - center) <= half_width].max())
    return total


def channel_features(
    x: Signal,
    sample_rate: float = SAMPLE_RATE_HZ,
    faults: FaultFrequencies = FAULT_FREQUENCIES,
) -> ChannelFeatures:
    x = x - x.mean()
    rms = float(np.sqrt(np.mean(x**2)))
    peak = float(np.max(np.abs(x)))
    freqs, amplitudes = envelope_spectrum(x, sample_rate)
    return ChannelFeatures(
        rms=rms,
        peak=peak,
        crest_factor=peak / rms,
        # Pearson's definition: 3 for Gaussian noise, the usual reference in vibration analysis.
        kurtosis=float(stats.kurtosis(x, fisher=False)),
        env_ftf=harmonic_amplitude(freqs, amplitudes, faults.ftf),
        env_bsf=harmonic_amplitude(freqs, amplitudes, faults.bsf),
        env_bpfo=harmonic_amplitude(freqs, amplitudes, faults.bpfo),
        env_bpfi=harmonic_amplitude(freqs, amplitudes, faults.bpfi),
    )

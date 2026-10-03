from dataclasses import asdict

import numpy as np
import pytest

from monitor.features import ChannelFeatures, channel_features
from monitor.ims import FAULT_FREQUENCIES, SAMPLE_RATE_HZ

FS = SAMPLE_RATE_HZ
T = np.arange(FS) / FS  # one second, like a snapshot


def noise(scale: float = 0.02, seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).normal(0, scale, T.size)


def impacts(rate_hz: float) -> np.ndarray:
    """A defect: short knocks at rate_hz, each ringing at 4 kHz like the housing, plus noise."""
    burst_t = np.arange(32) / FS
    burst = np.exp(-burst_t / 0.0003) * np.sin(2 * np.pi * 4_000 * burst_t)
    x = noise()
    for start in np.round(np.arange(0, 1, 1 / rate_hz) * FS).astype(int):
        end = min(start + burst.size, x.size)
        x[start:end] += burst[: end - start]
    return x


def strongest_fault(f: ChannelFeatures) -> str:
    amplitudes = {"ftf": f.env_ftf, "bsf": f.env_bsf, "bpfo": f.env_bpfo, "bpfi": f.env_bpfi}
    return max(amplitudes, key=amplitudes.__getitem__)


def test_sine_has_textbook_rms_peak_crest_factor_and_kurtosis():
    f = channel_features(0.5 * np.sin(2 * np.pi * 100 * T))

    assert f.rms == pytest.approx(0.5 / np.sqrt(2), rel=1e-3)
    assert f.peak == pytest.approx(0.5, rel=1e-3)
    assert f.crest_factor == pytest.approx(np.sqrt(2), rel=1e-3)
    assert f.kurtosis == pytest.approx(1.5, rel=1e-3)


def test_gaussian_noise_has_kurtosis_three():
    assert channel_features(noise()).kurtosis == pytest.approx(3, abs=0.15)


def test_dc_offset_does_not_change_the_features():
    x = impacts(FAULT_FREQUENCIES.bpfo)

    assert asdict(channel_features(x + 1.0)) == pytest.approx(asdict(channel_features(x)))


def test_knocks_raise_kurtosis_and_crest_factor_above_noise():
    defect, healthy = channel_features(impacts(FAULT_FREQUENCIES.bpfo)), channel_features(noise())

    assert defect.kurtosis > 2 * healthy.kurtosis
    # Gaussian noise already peaks at about 4 x its RMS over 20,480 samples.
    assert defect.crest_factor > 1.5 * healthy.crest_factor


@pytest.mark.parametrize(
    ("rate_hz", "fault"),
    [
        (FAULT_FREQUENCIES.bpfo, "bpfo"),
        (FAULT_FREQUENCIES.bpfi, "bpfi"),
        (FAULT_FREQUENCIES.bsf, "bsf"),
        # A roller defect strikes both races per spin.
        (2 * FAULT_FREQUENCIES.bsf, "bsf"),
        # Rollers slip, so the real rate sits slightly off the theoretical one.
        (FAULT_FREQUENCIES.bpfo * 1.005, "bpfo"),
    ],
)
def test_knock_rate_identifies_the_damaged_part(rate_hz, fault):
    assert strongest_fault(channel_features(impacts(rate_hz))) == fault


def test_steady_tone_at_bpfo_is_not_mistaken_for_a_defect():
    # Like the machine vibration near 237 Hz on every IMS bearing: louder than the knocks, but
    # without impacts it does not excite the resonance band.
    knocks = impacts(FAULT_FREQUENCIES.bpfo)
    tone = 0.5 * np.sin(2 * np.pi * FAULT_FREQUENCIES.bpfo * T) + noise()

    assert channel_features(tone).rms > channel_features(knocks).rms
    assert channel_features(tone).env_bpfo < 0.1 * channel_features(knocks).env_bpfo

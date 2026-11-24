"""Procedural pressure radiation from a shared, dispersive orthotropic piano soundboard."""

from functools import lru_cache

import numpy as np
from scipy.signal import butter, lfilter, sosfilt


# Successive bridge locations from the long bass strings to the short treble.
BRIDGE_KEYS = (21, 47, 71, 91, 108)
BRIDGE_POINTS = np.array([
    (0.28, 0.78), (0.40, 0.63), (0.54, 0.46), (0.67, 0.36), (0.79, 0.28),
])
MICROPHONE_POINTS = np.array([(0.35, 0.55), (0.65, 0.46)])
BRIDGE_CHANNELS = len(BRIDGE_KEYS)

# Early cabinet/lid/rim reflection tap times (ms) and signed gains per stereo mic.
_BODY_TAPS_MS = (
    (1.85, 3.95, 6.80, 10.60),
    (2.35, 4.75, 7.95, 12.10),
)
_BODY_TAP_GAINS = (
    (0.26, -0.21, 0.16, -0.11),
    (0.24, -0.22, 0.15, -0.12),
)

# Staggered biquad allpass frequencies (Hz) and pole radii modeling bending-wave
# phase dispersion across the ribbed spruce plate (slightly decorrelated L/R).
_ALLPASS_SPEC = (
    (
        (96.0, 0.952), (148.0, 0.958), (218.0, 0.964), (315.0, 0.964),
        (445.0, 0.962), (620.0, 0.958), (860.0, 0.954), (1210.0, 0.946),
        (1720.0, 0.935), (2480.0, 0.920), (3550.0, 0.900),
    ),
    (
        (110.0, 0.952), (168.0, 0.958), (246.0, 0.964), (352.0, 0.964),
        (495.0, 0.962), (685.0, 0.958), (945.0, 0.954), (1330.0, 0.946),
        (1890.0, 0.935), (2710.0, 0.920), (3820.0, 0.900),
    ),
)


@lru_cache(maxsize=128)
def bridge_position(pitch):
    """Return the two nearest bridge points and their interpolation weight."""
    position = np.interp(pitch, BRIDGE_KEYS, np.arange(BRIDGE_CHANNELS))
    left = min(int(position), BRIDGE_CHANNELS - 2)
    return left, np.float32(position - left)


def _allpass_sos(specs, sample_rate):
    """Build a cascade of exact unit-magnitude biquad allpass sections (float64)."""
    nyquist = 0.46 * sample_rate
    sections = []
    for freq_hz, radius in specs:
        if freq_hz >= nyquist:
            continue
        theta = 2.0 * np.pi * freq_hz / sample_rate
        # Keep group delay consistent across sample rates by scaling radius.
        r = radius ** (44100.0 / sample_rate)
        c = -2.0 * r * np.cos(theta)
        r2 = r * r
        sections.append([r2, c, 1.0, 1.0, c, r2])
    if not sections:
        return np.array([[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]], dtype=np.float64)
    return np.asarray(sections, dtype=np.float64)


@lru_cache(maxsize=8)
def _plate_design(sample_rate):
    # Orthotropic spruce thin-plate modes (f11 ≈ 70 Hz, ribbed cross-stiffness)
    # covering the sparse-to-medium modal region up to ~2.6 kHz.
    m, n = np.meshgrid(np.arange(1, 12), np.arange(1, 10), indexing="ij")
    long_axis, short_axis = 46.0 * m**2, 29.0 * n**2
    frequency = np.sqrt(long_axis**2 + short_axis**2 + 1.35 * long_axis * short_axis)
    keep = frequency < min(2600.0, 0.42 * sample_rate)
    hz = frequency[keep]
    shapes = np.column_stack((m[keep], n[keep]))

    def mode_shape(points):
        along = np.sin(np.pi * points[:, :1] * shapes[:, 0])
        across = np.sin(np.pi * points[:, 1:] * shapes[:, 1])
        return along * across

    bridge_modes = mode_shape(BRIDGE_POINTS).astype(np.float64)
    # Far-field microphone radiation weights lower-order plate patterns more
    # strongly than high-order short-wavelength ripples.
    area_average = np.exp(-0.011 * np.sum(shapes**2, axis=1))
    pressure_modes = (mode_shape(MICROPHONE_POINTS).T * area_average[:, None]).astype(np.float64)

    theta = 2.0 * np.pi * hz / sample_rate
    # Spruce plate damping: lush woody bloom (T60 ≈ 150-240 ms in bass/mid)
    # while dying below -40 dB within ~160 ms after damper release.
    loss = 26.0 + 0.036 * hz
    radius = np.exp(-loss / sample_rate)
    # Scale modal resonators so the spruce plate modes carry ~50-65% of the
    # radiated acoustic body energy (giving unmistakable grand piano woodiness).
    radiation_eff = 1.0 - np.exp(-hz / 105.0)
    leading = 9.5 * (1.0 - radius) * radiation_eff
    numerator = np.column_stack((leading, -leading * radius * np.cos(theta))).astype(np.float64)
    denominator = np.column_stack(
        (np.ones(len(hz)), -2.0 * radius * np.cos(theta), radius**2)
    ).astype(np.float64)

    # Bridge-to-board acoustic radiation shaping: soft low-end plate cutoff
    # (~52 Hz) and open, natural upper-register radiation (~7.8 kHz).
    hp = butter(1, min(52.0, 0.2 * sample_rate), btype="highpass", fs=sample_rate, output="sos")
    lp = butter(2, min(7800.0, 0.44 * sample_rate), btype="lowpass", fs=sample_rate, output="sos")
    radiation = np.vstack((hp, lp)).astype(np.float64)

    # Gentle lowpass on early cabinet/lid reflections (~3.2 kHz).
    body_lp = butter(
        1, min(3200.0, 0.40 * sample_rate), btype="lowpass", fs=sample_rate, output="sos"
    ).astype(np.float64)

    allpass_l = _allpass_sos(_ALLPASS_SPEC[0], sample_rate)
    allpass_r = _allpass_sos(_ALLPASS_SPEC[1], sample_rate)

    tap_delays = tuple(
        tuple(max(1, round(ms * 0.001 * sample_rate)) for ms in channel_ms)
        for channel_ms in _BODY_TAPS_MS
    )
    max_delay = max(max(delays) for delays in tap_delays)

    return (
        bridge_modes, pressure_modes, numerator, denominator,
        radiation, body_lp, allpass_l, allpass_r, tap_delays, max_delay,
    )


class PianoSoundboard:
    """Bridge force in, stereo sound pressure out, with one state per render."""

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        (
            self.bridge_modes,
            self.mode_pressure,
            self.numerator,
            self.denominator,
            self.radiation,
            self.body_lp,
            self.allpass_l,
            self.allpass_r,
            self.tap_delays,
            self.max_delay,
        ) = _plate_design(sample_rate)
        self.modal_state = np.zeros((len(self.numerator), 2), dtype=np.float64)
        self.radiation_state = np.zeros((len(self.radiation), 2, BRIDGE_CHANNELS), dtype=np.float64)
        self.allpass_state_l = np.zeros((len(self.allpass_l), 2), dtype=np.float64)
        self.allpass_state_r = np.zeros((len(self.allpass_r), 2), dtype=np.float64)
        self.body_lp_state = np.zeros((len(self.body_lp), 2, 2), dtype=np.float64)
        self.body_history = np.zeros((self.max_delay, 2), dtype=np.float64)
        self.diffuse_pressure = np.array(
            [[0.79, 0.32], [0.78, 0.49], [0.72, 0.67], [0.52, 0.78], [0.36, 0.79]],
            dtype=np.float64,
        )

    def reset(self):
        self.modal_state.fill(0.0)
        self.radiation_state.fill(0.0)
        self.allpass_state_l.fill(0.0)
        self.allpass_state_r.fill(0.0)
        self.body_lp_state.fill(0.0)
        self.body_history.fill(0.0)

    def _early_body_reflections(self, stereo):
        """Block-invariant multi-tap cabinet/lid early reflections."""
        frames = len(stereo)
        if frames == 0:
            return np.zeros((0, 2), dtype=np.float64)
        filtered, self.body_lp_state = sosfilt(
            self.body_lp, stereo, axis=0, zi=self.body_lp_state
        )
        buf = np.vstack((self.body_history, filtered))
        self.body_history = buf[-self.max_delay:].copy()
        reflections = np.zeros((frames, 2), dtype=np.float64)
        base = self.max_delay
        for ch in range(2):
            for delay, gain in zip(self.tap_delays[ch], _BODY_TAP_GAINS[ch]):
                start = base - delay
                reflections[:, ch] += gain * buf[start:start + frames, ch]
        return reflections

    def process(self, force, resonance):
        force64 = np.asarray(force, dtype=np.float64)
        frames = len(force64)
        if frames == 0:
            return np.zeros((0, 2), dtype=np.float32)

        radiation, self.radiation_state = sosfilt(
            self.radiation, force64, axis=0, zi=self.radiation_state
        )

        # 1. Broadband plate path routed through L/R bending-wave allpass
        #    dispersion to eliminate raw piezo-like periodic spikes.
        raw_stereo = np.einsum("fb,bc->fc", radiation, self.diffuse_pressure)
        disp_l, self.allpass_state_l = sosfilt(
            self.allpass_l, raw_stereo[:, 0], zi=self.allpass_state_l
        )
        disp_r, self.allpass_state_r = sosfilt(
            self.allpass_r, raw_stereo[:, 1], zi=self.allpass_state_r
        )
        dispersed = np.column_stack((disp_l, disp_r))

        # 2. Early acoustic rim/lid reflections (block-invariant delay buffer).
        early = self._early_body_reflections(dispersed)

        # 3. Orthotropic spruce plate modal resonances (dominant body coloration).
        modal_force = np.einsum("fb,bm->mf", radiation, self.bridge_modes)
        for index, drive in enumerate(modal_force):
            response, self.modal_state[index] = lfilter(
                self.numerator[index], self.denominator[index], drive, zi=self.modal_state[index]
            )
            modal_force[index] = response
        modal_stereo = np.einsum("mf,mc->fc", modal_force, self.mode_pressure)

        # Blend dispersed broadband radiation, early body reflections, and
        # resonant spruce plate modes (modulated by soundboard_resonance).
        res = float(resonance)
        stereo = (
            0.46 * dispersed
            + (0.38 + 0.36 * res) * early
            + (0.38 + 0.68 * res) * modal_stereo
        )
        return stereo.astype(np.float32)

"""Procedural pressure radiation from a shared, passive dispersive spruce piano soundboard."""

from functools import lru_cache

import numpy as np
from scipy.signal import butter, lfilter, sosfilt


# Successive bridge locations from the long copper-wound bass strings to the short treble.
BRIDGE_KEYS = (21, 47, 71, 91, 108)
BRIDGE_POINTS = np.array([
    (0.26, 0.76), (0.39, 0.62), (0.53, 0.47), (0.66, 0.36), (0.79, 0.27),
])
MICROPHONE_POINTS = np.array([(0.34, 0.56), (0.66, 0.44)])
BRIDGE_CHANNELS = len(BRIDGE_KEYS)

# Open-lid and curved-rim acoustic reflection delays (ms) and gentle diffuse gains
# calibrated to a ~2.1 m concert grand geometry (avoiding short 2 ms guitar-box comb notches).
_BODY_TAPS_MS = (
    (7.30, 12.70, 19.10, 27.30),
    (8.60, 14.30, 21.40, 29.80),
)
_BODY_TAP_GAINS = (
    (0.068, -0.052, 0.038, -0.026),
    (0.065, -0.054, 0.036, -0.028),
)

# Staggered unit-magnitude biquad allpass sections modeling orthotropic bending-wave
# phase dispersion across the ribbed Sitka spruce soundboard (decorrelated L/R).
_ALLPASS_SPEC = (
    (
        (68.0, 0.984), (104.0, 0.985), (154.0, 0.985), (224.0, 0.984),
        (322.0, 0.982), (456.0, 0.980), (638.0, 0.978), (890.0, 0.974),
        (1235.0, 0.970), (1715.0, 0.964), (2380.0, 0.958), (3310.0, 0.950),
        (4580.0, 0.942), (6250.0, 0.934), (8350.0, 0.926), (10800.0, 0.918),
    ),
    (
        (77.0, 0.984), (118.0, 0.985), (174.0, 0.985), (252.0, 0.984),
        (360.0, 0.982), (508.0, 0.980), (710.0, 0.978), (988.0, 0.974),
        (1368.0, 0.970), (1895.0, 0.964), (2625.0, 0.958), (3640.0, 0.950),
        (5020.0, 0.942), (6780.0, 0.934), (8950.0, 0.926), (11400.0, 0.918),
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
        r = radius ** (44100.0 / sample_rate)
        c = -2.0 * r * np.cos(theta)
        r2 = r * r
        sections.append([r2, c, 1.0, 1.0, c, r2])
    if not sections:
        return np.array([[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]], dtype=np.float64)
    return np.asarray(sections, dtype=np.float64)


@lru_cache(maxsize=8)
def _plate_design(sample_rate):
    # Curved-bentside ribbed Sitka spruce plate modes (~68 Hz to ~2.2 kHz).
    # Non-rectangular rim asymmetry prevents accidental modal pile-ups (wolf notes).
    m, n = np.meshgrid(np.arange(1, 10), np.arange(1, 8), indexing="ij")
    asym = 1.0 + 0.035 * np.sin(1.7 * m + 2.3 * n) + 0.024 * np.cos(2.9 * m - 1.3 * n)
    long_axis = 44.0 * (m ** 1.90)
    short_axis = 31.5 * (n ** 1.92)
    raw_freq = asym * np.sqrt(long_axis**2 + short_axis**2 + 1.40 * long_axis * short_axis)
    flat_f = raw_freq.ravel()
    flat_m = m.ravel()
    flat_n = n.ravel()
    order = np.argsort(flat_f)
    flat_f, flat_m, flat_n = flat_f[order], flat_m[order], flat_n[order]
    keep = flat_f < min(2250.0, 0.42 * sample_rate)
    hz_raw = flat_f[keep]
    # Curved-bentside Wigner-Dyson level repulsion (Ege & Boutillon, JASA):
    # prevents rectangular lattice degeneracies from piling up into single-key wolf notes.
    weyl_grid = np.linspace(hz_raw[0], hz_raw[-1], len(hz_raw))
    hz = 0.35 * hz_raw + 0.65 * weyl_grid
    shapes = np.column_stack((flat_m[keep], flat_n[keep]))

    # Distributed laminated-maple bridge & cross-rib spatial footprint:
    # strictly positive passive energy coupling prevents nodal dead spots on any key.
    offsets = ((-0.045, -0.038), (0.0, 0.0), (0.045, 0.038))
    weights = (0.25, 0.50, 0.25)

    def passive_coupling(points):
        acc = np.zeros((len(points), len(shapes)), dtype=np.float64)
        for (dx, dy), w in zip(offsets, weights):
            px = np.clip(points[:, :1] + dx, 0.05, 0.95)
            py = np.clip(points[:, 1:] + dy, 0.05, 0.95)
            phi = np.sin(np.pi * px * shapes[:, 0]) * np.sin(np.pi * py * shapes[:, 1])
            acc += w * (phi ** 2)
        return np.sqrt(acc + 0.045)

    bridge_modes = passive_coupling(BRIDGE_POINTS)
    area_average = np.exp(-0.019 * np.sum(shapes**2, axis=1))
    mic_coupling = passive_coupling(MICROPHONE_POINTS).T
    pan = 0.5 + 0.28 * np.sin(1.3 * shapes[:, 0] - 0.9 * shapes[:, 1])
    stereo_pan = np.column_stack((pan, 1.0 - pan)) * 1.42
    pressure_modes = (mic_coupling * stereo_pan * area_average[:, None]).astype(np.float64)

    theta = 2.0 * np.pi * hz / sample_rate
    # Sitka spruce internal friction + radiation loss: overlapping woody plate modes
    # above ~220 Hz with fast post-damper settling (< -50 dB within 180 ms).
    loss = 28.0 + 0.110 * hz
    radius = np.exp(-loss / sample_rate)
    radiation_eff = (1.0 - np.exp(-hz / 82.0)) / np.sqrt(1.0 + (hz / 580.0) ** 2)
    # Passive velocity bandpass numerator g * (1 - z^-2): Re{H(e^jw)} >= 0 everywhere,
    # guaranteeing that modal resonances enrich woodiness without cancelling direct mobility.
    gain = 1.05 * (1.0 - radius**2) * radiation_eff
    numerator = np.column_stack((gain, np.zeros(len(hz)), -gain)).astype(np.float64)
    denominator = np.column_stack(
        (np.ones(len(hz)), -2.0 * radius * np.cos(theta), radius**2)
    ).astype(np.float64)

    # Full-range concert-grand radiation bandwidth (~44 Hz chest to ~12.2 kHz treble air).
    hp = butter(1, min(44.0, 0.2 * sample_rate), btype="highpass", fs=sample_rate, output="sos")
    lp = butter(2, min(12200.0, 0.45 * sample_rate), btype="lowpass", fs=sample_rate, output="sos")
    radiation = np.vstack((hp, lp)).astype(np.float64)

    # Warm acoustic absorption on open-lid / case rim reflections (~2.7 kHz).
    body_lp = butter(
        1, min(2700.0, 0.40 * sample_rate), btype="lowpass", fs=sample_rate, output="sos"
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
    """Bridge force in, stereo sound pressure out, with one continuous state per render."""

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
            [[0.80, 0.34], [0.76, 0.50], [0.67, 0.67], [0.50, 0.76], [0.34, 0.80]],
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
        """Block-invariant multi-tap open-lid and case-rim early reflections."""
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

        # 1. Skudrzyk characteristic plate mobility + passive spruce modal resonances.
        #    Summing passive velocity modes before bending-wave dispersion prevents
        #    out-of-phase comb notches that otherwise mute individual keys.
        direct_stereo = np.einsum("fb,bc->fc", radiation, self.diffuse_pressure)
        modal_force = np.einsum("fb,bm->mf", radiation, self.bridge_modes)
        for index, drive in enumerate(modal_force):
            response, self.modal_state[index] = lfilter(
                self.numerator[index], self.denominator[index], drive, zi=self.modal_state[index]
            )
            modal_force[index] = response
        modal_stereo = np.einsum("mf,mc->fc", modal_force, self.mode_pressure)

        res = float(resonance)
        plate_stereo = 0.52 * direct_stereo + (0.32 + 0.58 * res) * modal_stereo

        # 2. Propagate unified plate velocity through L/R dispersive spruce bending waves.
        disp_l, self.allpass_state_l = sosfilt(
            self.allpass_l, plate_stereo[:, 0], zi=self.allpass_state_l
        )
        disp_r, self.allpass_state_r = sosfilt(
            self.allpass_r, plate_stereo[:, 1], zi=self.allpass_state_r
        )
        dispersed = np.column_stack((disp_l, disp_r))

        # 3. Subtle open-lid and case-rim reflections for 3D acoustic body depth.
        early = self._early_body_reflections(dispersed)
        stereo = dispersed + (0.34 + 0.44 * res) * early
        return stereo.astype(np.float32)

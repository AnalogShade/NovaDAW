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

# Mutually prime soundboard-cavity & undamped-harp waveguide delays (ms) across L/R.
# Responds sympathetically to any pitch/chord across all 88 keys with zero metallic periodicity.
_RESONATOR_DELAYS_MS = (16.7, 21.3, 26.9, 32.3, 38.9, 44.3)
_RESONATOR_STEREO = np.array([
    (0.82, 0.24), (0.26, 0.80), (0.74, 0.38),
    (0.36, 0.76), (0.66, 0.48), (0.46, 0.68),
], dtype=np.float64)

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
def _plate_geometry(sample_rate):
    """Static spatial mode shapes, filters, and waveguide delays for a given sample rate."""
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
    plate_hz = 0.35 * hz_raw + 0.65 * weyl_grid
    shapes = np.column_stack((flat_m[keep], flat_n[keep]))

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
    mode_pressure = (mic_coupling * stereo_pan * area_average[:, None]).astype(np.float64)

    # Full-range concert-grand radiation bandwidth (~44 Hz chest to ~12.2 kHz treble air).
    hp = butter(1, min(44.0, 0.2 * sample_rate), btype="highpass", fs=sample_rate, output="sos")
    lp = butter(2, min(12200.0, 0.45 * sample_rate), btype="lowpass", fs=sample_rate, output="sos")
    radiation = np.vstack((hp, lp)).astype(np.float64)

    # Bandpass shaping for the sympathetic soundboard & undamped harp halo (85 Hz - 2400 Hz).
    symp_bp = butter(
        2, [min(85.0, 0.15 * sample_rate), min(2400.0, 0.40 * sample_rate)],
        btype="bandpass", fs=sample_rate, output="sos",
    ).astype(np.float64)

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

    wg_delays = tuple(max(4, round(ms * 0.001 * sample_rate)) for ms in _RESONATOR_DELAYS_MS)

    return (
        plate_hz, bridge_modes, mode_pressure,
        radiation, symp_bp, body_lp,
        allpass_l, allpass_r, tap_delays, max_delay, wg_delays,
    )


@lru_cache(maxsize=64)
def _modal_resonators(sample_rate, res_key):
    """Compute passive spruce modal, chest shelf, & sympathetic waveguide coefficients."""
    plate_hz, *_, wg_delays = _plate_geometry(sample_rate)
    res = float(res_key)

    # 0. Exact bilinear Sitka-spruce body-vs-presence tilt (exact 0.0 dB identity at 67%):
    #    - 0%: -5.5 dB leaner wood chest (<320 Hz) + +2.5 dB crisp studio string presence (>900 Hz)
    #    - 67%: exact 1.0 unity transfer
    #    - 100%: +5.0 dB deep resonant Sitka spruce body (<320 Hz) + velvet top (-1.8 dB >900 Hz)
    low_db = float(np.interp(res, (0.0, 0.67, 1.0), (-5.5, 0.0, 5.0)))
    high_db = float(np.interp(res, (0.0, 0.67, 1.0), (2.5, 0.0, -1.8)))
    gl = 10.0 ** (low_db / 20.0)
    kl = np.tan(np.pi * min(320.0, 0.25 * sample_rate) / sample_rate)
    low_sos = [
        (1.0 + gl * kl) / (1.0 + kl),
        (gl * kl - 1.0) / (1.0 + kl),
        0.0, 1.0, (kl - 1.0) / (1.0 + kl), 0.0,
    ]
    gh = 10.0 ** (high_db / 20.0)
    kh = np.tan(np.pi * min(950.0, 0.35 * sample_rate) / sample_rate)
    high_sos = [
        (gh + kh) / (1.0 + kh),
        (kh - gh) / (1.0 + kh),
        0.0, 1.0, (kh - 1.0) / (1.0 + kh), 0.0,
    ]
    chest_sos = np.array([low_sos, high_sos], dtype=np.float64)

    # 1. Sitka spruce plate modes:
    #    - 0%: tight, heavily damped studio table (loss ~72/s, minimal low-wood bloom)
    #    - 67%: balanced concert grand (loss ~28/s)
    #    - 100%: deep, resonant, singing spruce chest (loss ~11/s + warm woody body)
    base_loss = float(np.interp(res, (0.0, 0.67, 1.0), (72.0, 28.0, 11.5)))
    freq_loss = float(np.interp(res, (0.0, 0.67, 1.0), (0.195, 0.110, 0.048)))
    loss_p = base_loss + freq_loss * plate_hz
    radius_p = np.exp(-loss_p / sample_rate)
    theta_p = 2.0 * np.pi * plate_hz / sample_rate

    wood_chest_boost = 1.0 + float(np.interp(res, (0.0, 0.67, 1.0), (-0.50, 0.0, 0.85))) * np.exp(
        -((plate_hz - 165.0) / 185.0) ** 2
    )
    cutoff_hz = float(np.interp(res, (0.0, 0.67, 1.0), (360.0, 580.0, 760.0)))
    rad_eff = (1.0 - np.exp(-plate_hz / 82.0)) / np.sqrt(1.0 + (plate_hz / cutoff_hz) ** 2)
    plate_scale = float(np.interp(res, (0.0, 0.67, 1.0), (0.14, 1.05, 1.32)))
    gain_p = plate_scale * wood_chest_boost * (1.0 - radius_p ** 2) * rad_eff

    numerator = np.column_stack((gain_p, np.zeros(len(gain_p)), -gain_p)).astype(np.float64)
    denominator = np.column_stack(
        (np.ones(len(gain_p)), -2.0 * radius_p * np.cos(theta_p), radius_p ** 2)
    ).astype(np.float64)

    # 2. Energy-normalized sympathetic soundboard & undamped-harp waveguide bank:
    #    - 0%: silent (dry damped piano).
    #    - 67%: T60 = 0.16s (subtle body bloom, >45 dB decay within 200 ms after damper drop).
    #    - 100%: T60 = 2.05s (lush, unmistakable woody & sympathetic string halo without clipping!).
    t60_symp = float(np.interp(res, (0.0, 0.45, 0.67, 0.78, 1.0), (0.03, 0.10, 0.16, 0.85, 2.05)))
    damp_pole = float(0.24 ** (44100.0 / sample_rate))
    wg_pairs = []
    for delay in wg_delays:
        delay_sec = delay / sample_rate
        loop_gain = min(0.990, 10.0 ** (-3.0 * delay_sec / max(0.02, t60_symp)))
        norm = np.sqrt(max(1e-6, 1.0 - loop_gain ** 2))
        b = np.array([norm, -norm * damp_pole], dtype=np.float64)
        a = np.zeros(delay + 1, dtype=np.float64)
        a[0] = 1.0
        a[1] = -damp_pole
        a[delay] = -loop_gain * (1.0 - damp_pole)
        wg_pairs.append((b, a))

    return chest_sos, numerator, denominator, tuple(wg_pairs)


class PianoSoundboard:
    """Bridge force in, stereo sound pressure out, with one continuous state per render."""

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        (
            self.plate_hz,
            self.bridge_modes,
            self.mode_pressure,
            self.radiation,
            self.symp_bp,
            self.body_lp,
            self.allpass_l,
            self.allpass_r,
            self.tap_delays,
            self.max_delay,
            self.wg_delays,
        ) = _plate_geometry(sample_rate)
        self.modal_state = np.zeros((len(self.plate_hz), 2), dtype=np.float64)
        self.radiation_state = np.zeros((len(self.radiation), 2, BRIDGE_CHANNELS), dtype=np.float64)
        self.chest_state = np.zeros((2, 2, 2), dtype=np.float64)
        self.symp_bp_state = np.zeros((len(self.symp_bp), 2), dtype=np.float64)
        self.wg_states = [np.zeros(d, dtype=np.float64) for d in self.wg_delays]
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
        self.chest_state.fill(0.0)
        self.symp_bp_state.fill(0.0)
        for st in self.wg_states:
            st.fill(0.0)
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

        res = float(np.clip(resonance, 0.0, 1.0))
        res_key = round(res, 2)
        chest_sos, numerator, denominator, wg_pairs = _modal_resonators(self.sample_rate, res_key)

        radiation, self.radiation_state = sosfilt(
            self.radiation, force64, axis=0, zi=self.radiation_state
        )

        # 1. Direct string-to-bridge mobility.
        direct_stereo = np.einsum("fb,bc->fc", radiation, self.diffuse_pressure)

        # 2. Passive Sitka spruce plate modal resonances (woody chest & body warmth).
        modal_force = np.einsum("fb,bm->mf", radiation, self.bridge_modes)
        for index, drive in enumerate(modal_force):
            response, self.modal_state[index] = lfilter(
                numerator[index], denominator[index], drive, zi=self.modal_state[index]
            )
            modal_force[index] = response
        modal_stereo = np.einsum("mf,mc->fc", modal_force, self.mode_pressure)

        # 3. Delayed sympathetic soundboard & undamped harp halo (zero direct feedthrough
        #    so attacks never clip while sympathetic strings & wood bloom lushly after onset).
        bridge_mono = radiation.sum(axis=1) * 0.45
        symp_drive, self.symp_bp_state = sosfilt(
            self.symp_bp, bridge_mono, zi=self.symp_bp_state
        )
        symp_stereo = np.zeros((frames, 2), dtype=np.float64)
        symp_mix = float(np.interp(res, (0.0, 0.35, 0.67, 0.78, 1.0), (0.0, 0.015, 0.035, 0.38, 0.95)))
        for idx, (wg_b, wg_a) in enumerate(wg_pairs):
            wg_out, self.wg_states[idx] = lfilter(wg_b, wg_a, symp_drive, zi=self.wg_states[idx])
            if symp_mix > 0.0:
                wg_tail = wg_out - wg_b[0] * symp_drive
                symp_stereo += wg_tail[:, None] * _RESONATOR_STEREO[idx]

        direct_gain = float(np.interp(res, (0.0, 0.67, 1.0), (0.82, 0.52, 0.34)))
        modal_gain = float(np.interp(res, (0.0, 0.67, 1.0), (0.10, 0.71, 0.68)))
        plate_raw = direct_gain * direct_stereo + modal_gain * modal_stereo + symp_mix * symp_stereo
        plate_stereo, self.chest_state = sosfilt(
            chest_sos, plate_raw, axis=0, zi=self.chest_state
        )

        # 4. Propagate unified plate & sympathetic vibration through dispersive spruce bending waves.
        disp_l, self.allpass_state_l = sosfilt(
            self.allpass_l, plate_stereo[:, 0], zi=self.allpass_state_l
        )
        disp_r, self.allpass_state_r = sosfilt(
            self.allpass_r, plate_stereo[:, 1], zi=self.allpass_state_r
        )
        dispersed = np.column_stack((disp_l, disp_r))

        # 5. Open-lid & case-rim 3D body reflections (closed/dry at 0%, wide open at 100%).
        early = self._early_body_reflections(dispersed)
        lid_gain = float(np.interp(res, (0.0, 0.67, 1.0), (0.04, 0.63, 1.35)))
        stereo = dispersed + lid_gain * early
        return stereo.astype(np.float32)

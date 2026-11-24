"""Register calibration for the procedural concert grand piano. All values are scalar parameters."""

import numpy as np


KEYS = np.arange(24, 109, 6)
# Stiff-string inharmonicity coefficients B from measured Steinway Model B scaling.
INHARMONICITY = np.array([
    .000247, .000127, .000140, .000140, .000114, .000191, .000303, .000591,
    .001094, .001343, .002616, .003381, .0058, .0059, .00896,
])
# Singing aftersound (slow-polarization) decay rates in 1/s (T60 ≈ 6.91 / loss):
# ~27s at C1, ~20s at C2, ~14s at C3, ~9.0s at C4, ~5.4s at C5, ~3.0s at C6.
LATE_LOSS = np.array([
    .25, .29, .34, .41, .50, .62, .77, .98,
    1.28, 1.72, 2.30, 3.00, 3.80, 4.70, 5.70,
])
# Balanced Weinreich coupled-unison share in the vertical prompt polarization:
# clear two-stage piano bloom at attack with rich >=44-50% singing trichord sustain.
PROMPT_SHARE = np.array([
    .58, .59, .60, .58, .56, .54, .53, .52,
    .51, .50, .49, .48, .47, .46, .46,
])
MODE_BANDS = (1, 2, 3, 4, 5, 7, 10, 14, 20, 29, 42, 62, 90, 133)


# Concert-grand modal voicing in dB per calibration key and partial group in MODE_BANDS,
# applied atop the physical string-to-bridge impedance transfer in piano_dsp.py.
# Calibrated so:
# - Deep bass (24-36): full H1-H6 Steinway chest + clear copper-wound upper partials (8-30).
# - Tenor/midrange (42-66): solid fundamental paired with luminous, singing H2-H8 overtones
#   (never a hollow sine + knock like a plucked nylon guitar, nor a nasal mid-harmonic bump).
# - Treble (72-108): crystalline bell overtones (H2-H5 at -10..-20 dB) with smooth air.
MEDIUM_DB = np.array([
    ( -8.5,   0.0,   1.6,   2.2,   2.6,   5.2,   9.2,  11.5,  10.8,   7.2,   1.0,  -7.0, -15.5, -24.0),  # MIDI 24
    ( -6.5,   0.5,   1.6,   2.0,   2.5,   5.2,   9.5,  11.8,  10.8,   6.8,   0.0,  -8.0, -16.5, -25.0),  # MIDI 30
    ( -3.5,   1.0,   1.5,   1.6,   2.2,   5.5,  10.2,  12.2,  10.8,   6.2,  -1.0,  -9.0, -17.5, -26.0),  # MIDI 36
    ( -0.5,   1.8,   1.8,   1.5,   1.5,   2.4,   4.0,   4.2,   1.8,  -4.0, -11.0, -18.5, -26.0, -33.0),  # MIDI 42
    (  1.5,   2.2,   1.8,   1.2,   0.5,  -0.6,  -2.2,  -4.8,  -8.8, -14.0, -20.0, -26.0, -32.0, -37.0),  # MIDI 48
    (  2.2,   2.2,   1.6,   0.8,  -0.2,  -1.8,  -4.2,  -7.5, -11.8, -17.2, -23.0, -29.0, -34.0, -38.0),  # MIDI 54
    (  2.8,   2.2,   1.4,   0.4,  -0.8,  -3.0,  -6.0,  -9.8, -14.8, -20.5, -26.5, -32.0, -37.0, -40.0),  # MIDI 60
    (  3.2,   2.0,   1.0,  -0.2,  -1.6,  -4.2,  -7.8, -12.0, -17.5, -23.5, -29.0, -34.0, -38.0, -40.0),  # MIDI 66
    (  3.6,   1.6,   0.4,  -1.2,  -3.0,  -6.0, -10.0, -15.0, -20.5, -26.5, -32.0, -36.0, -40.0, -40.0),  # MIDI 72
    (  4.2,   1.0,  -0.6,  -2.6,  -4.8,  -8.2, -12.8, -18.0, -23.5, -29.0, -34.0, -38.0, -40.0, -40.0),  # MIDI 78
    (  4.8,   0.2,  -1.8,  -4.2,  -6.8, -10.8, -15.5, -21.0, -26.5, -32.0, -36.0, -40.0, -40.0, -40.0),  # MIDI 84
    (  5.4,  -0.4,  -2.8,  -5.8,  -8.8, -13.0, -18.0, -23.5, -29.0, -34.0, -38.0, -40.0, -40.0, -40.0),  # MIDI 90
    (  6.0,  -0.8,  -3.8,  -7.2, -10.8, -15.5, -20.5, -26.0, -31.5, -36.0, -40.0, -40.0, -40.0, -40.0),  # MIDI 96
    (  6.6,  -1.2,  -4.8,  -8.5, -12.5, -17.5, -22.5, -28.0, -33.5, -38.0, -40.0, -40.0, -40.0, -40.0),  # MIDI 102
    (  7.2,  -1.5,  -5.8, -10.0, -14.0, -19.0, -24.0, -30.0, -35.0, -38.0, -40.0, -40.0, -40.0, -40.0),  # MIDI 108
], dtype=np.float32)

# Forte brightening (velocity > 80): nonlinear wool-felt compression opens upper partials smoothly.
LOUD_DELTA_DB = np.array([
    ( -0.8,  -0.4,   0.0,   0.4,   0.8,   1.6,   2.8,   4.0,   5.2,   6.0,   6.5,   5.5,   4.0,   2.0),  # MIDI 24
    ( -0.8,  -0.4,   0.0,   0.4,   0.8,   1.8,   3.0,   4.2,   5.4,   6.0,   6.2,   5.0,   3.5,   2.0),  # MIDI 30
    ( -0.6,   0.0,   0.2,   0.6,   1.0,   2.0,   3.2,   4.5,   5.5,   6.0,   5.8,   4.5,   3.0,   1.5),  # MIDI 36
    ( -0.5,   0.0,   0.4,   1.0,   1.8,   3.0,   4.4,   5.8,   6.8,   6.8,   5.8,   4.0,   2.0,   0.0),  # MIDI 42
    ( -0.5,   0.2,   0.8,   1.6,   2.8,   4.5,   6.5,   8.2,   9.0,   8.0,   6.0,   3.5,   0.0,   0.0),  # MIDI 48
    ( -0.4,   0.4,   1.2,   2.2,   3.5,   5.2,   7.2,   8.8,   8.5,   6.5,   4.0,   0.0,   0.0,   0.0),  # MIDI 54
    ( -0.3,   0.6,   1.6,   2.8,   4.2,   6.0,   8.0,   8.8,   7.5,   4.5,   0.0,   0.0,   0.0,   0.0),  # MIDI 60
    (  0.0,   0.8,   2.0,   3.4,   5.0,   6.8,   8.2,   7.5,   4.5,   0.0,   0.0,   0.0,   0.0,   0.0),  # MIDI 66
    (  0.0,   1.2,   2.6,   4.2,   5.8,   7.2,   6.8,   4.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0),  # MIDI 72
    (  0.0,   1.6,   3.2,   4.8,   6.0,   6.0,   3.5,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0),  # MIDI 78
    (  0.0,   2.0,   3.8,   5.0,   5.0,   3.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0),  # MIDI 84
    (  0.0,   2.4,   4.0,   4.0,   2.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0),  # MIDI 90
    (  0.0,   2.5,   3.2,   2.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0),  # MIDI 96
    (  0.0,   2.0,   2.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0),  # MIDI 102
    (  0.0,   1.5,   1.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0,   0.0),  # MIDI 108
], dtype=np.float32)

# Piano darkening (velocity < 80): plush outer wool felt gently rounds off upper harmonics.
SOFT_DELTA_DB = np.array([
    (  1.5,   0.8,   0.0,  -1.0,  -2.2,  -4.0,  -6.5,  -9.5, -13.0, -16.5, -19.0, -21.0, -22.0, -22.0),  # MIDI 36
    (  1.0,  -1.2,  -3.2,  -5.5,  -8.0, -11.0, -14.0, -16.5, -18.0, -18.0, -16.0, -16.0, -16.0, -16.0),  # MIDI 60
    (  0.5,  -2.5,  -5.5,  -8.5, -11.0, -12.0, -12.0, -10.0, -10.0, -10.0, -10.0, -10.0, -10.0, -10.0),  # MIDI 84
], dtype=np.float32)

SOFT_KEYS = (36, 60, 84)


def band_indices(partials):
    return np.searchsorted(MODE_BANDS[1:], partials, side="right")


def _interpolate_row(pitch, keys, rows):
    upper = np.searchsorted(keys, pitch, side="right")
    if upper == 0:
        return rows[0]
    if upper == len(keys):
        return rows[-1]
    blend = (pitch - keys[upper - 1]) / (keys[upper] - keys[upper - 1])
    return rows[upper - 1] * (1 - blend) + rows[upper] * blend


def modal_voicing(pitch, velocity, bands):
    """Smooth the reference-derived gain across keys and playing dynamics."""
    medium = _interpolate_row(pitch, KEYS, MEDIUM_DB)
    if velocity < 80:
        color = _interpolate_row(pitch, SOFT_KEYS, SOFT_DELTA_DB)
        blend = min(1.0, (80 - velocity) / 35)
    else:
        color = _interpolate_row(pitch, KEYS, LOUD_DELTA_DB)
        blend = min(1.0, (velocity - 80) / 35)
    return 10 ** ((medium + color * blend)[bands] / 20)

"""Register calibration for the procedural grand piano. All values are scalar parameters."""

import numpy as np


KEYS = np.arange(24, 109, 6)
# Stiff-string coefficients estimated from the Steinway B at the University of Iowa.
INHARMONICITY = np.array([
    .000247, .000127, .000140, .000140, .000114, .000191, .000303, .000591,
    .001094, .001343, .002616, .003381, .0058, .0059, .00896,
])
# Smooth aftersound (slow-branch) decay rates in 1/s (T60 ≈ 6.91 / loss):
# ~26s at C1, ~20s at C2, ~13s at C3, ~8s at C4, ~4.8s at C5, ~2.6s at C6.
LATE_LOSS = np.array([
    .26, .30, .35, .42, .52, .66, .85, 1.10,
    1.45, 1.95, 2.65, 3.40, 4.30, 5.40, 6.80,
])
# Initial amplitude share in the fast-decaying symmetric prompt branch
# (Weinreich coupled-string two-stage decay across the entire keyboard).
PROMPT_SHARE = np.array([
    .54, .58, .62, .65, .68, .70, .72, .74,
    .76, .78, .80, .77, .73, .69, .66,
])
MODE_BANDS = (1, 2, 3, 4, 5, 7, 10, 14, 20, 29, 42, 62, 90, 133)


# Smooth grand-piano modal voicing corrections in dB per calibration key and
# partial group in MODE_BANDS. Balances warm lower partials (deep grand-piano
# chest) with open-lid Steinway upper-harmonic clarity (no spikes or comb holes).
MEDIUM_DB = np.array([
    (-15,  -7,  -4,  -3,  -2,   2,   7,   8,   6,   2,  -5, -14, -22, -30),  # MIDI 24
    (-13,  -6,  -3,  -2,  -1,   3,   7,   8,   6,   1,  -6, -15, -23, -31),  # MIDI 30
    (-11,  -6,  -4,  -3,  -1,   3,   8,   9,   8,   4,  -3, -12, -20, -28),  # MIDI 36
    ( -4,  -2,  -2,  -1,   0,   2,   5,   4,   1,  -5, -13, -21, -29, -35),  # MIDI 42
    (  1,   1,   0,  -1,   0,   1,   1,  -3, -10, -17, -25, -33, -38, -40),  # MIDI 48
    (  4,   2,   0,   0,   0,   1,  -2,  -8, -16, -24, -31, -36, -40, -40),  # MIDI 54
    (  7,   3,   0,   1,   2,   2,  -4, -11, -19, -27, -34, -38, -40, -40),  # MIDI 60
    ( 11,   3,  -1,  -2,  -3,  -7, -14, -22, -29, -35, -38, -40, -40, -40),  # MIDI 66
    ( 13,   0,  -4,  -8, -13, -19, -26, -32, -36, -38, -40, -40, -40, -40),  # MIDI 72
    ( 15,  -1,  -8, -14, -20, -26, -32, -36, -38, -40, -40, -40, -40, -40),  # MIDI 78
    ( 17,  -2, -11, -18, -24, -30, -34, -38, -40, -40, -40, -40, -40, -40),  # MIDI 84
    ( 19,  -5, -15, -22, -28, -32, -36, -38, -40, -40, -40, -40, -40, -40),  # MIDI 90
    ( 20,  -6, -16, -24, -30, -34, -38, -40, -40, -40, -40, -40, -40, -40),  # MIDI 96
    ( 22,  -4, -14, -22, -28, -32, -36, -38, -40, -40, -40, -40, -40, -40),  # MIDI 102
    ( 24,  -4, -14, -22, -28, -32, -36, -38, -40, -40, -40, -40, -40, -40),  # MIDI 108
], dtype=np.float32)

# Forte brightening (velocity > 80): nonlinear felt hardening excites upper partials.
LOUD_DELTA_DB = np.array([
    ( -2,  -1,   0,   0,   1,   3,   5,   8,  11,  14,  16,  14,  10,   6),  # MIDI 24
    ( -2,  -1,   0,   0,   1,   3,   5,   8,  11,  14,  15,  12,   8,   4),  # MIDI 30
    ( -1,  -1,   0,   1,   2,   4,   6,   9,  12,  14,  13,  10,   6,   2),  # MIDI 36
    ( -1,   0,   0,   1,   3,   5,   8,  11,  13,  14,  11,   8,   4,   0),  # MIDI 42
    ( -1,   0,   1,   2,   4,   6,   9,  12,  14,  12,   8,   4,   0,   0),  # MIDI 48
    ( -1,   0,   1,   3,   5,   8,  11,  13,  13,   9,   5,   0,   0,   0),  # MIDI 54
    ( -1,   1,   2,   4,   6,   9,  13,  14,  11,   6,   0,   0,   0,   0),  # MIDI 60
    (  0,   1,   3,   5,   8,  11,  13,  11,   6,   0,   0,   0,   0,   0),  # MIDI 66
    (  0,   2,   5,   8,  10,  12,   9,   4,   0,   0,   0,   0,   0,   0),  # MIDI 72
    (  0,   3,   6,   8,  10,   9,   4,   0,   0,   0,   0,   0,   0,   0),  # MIDI 78
    (  0,   4,   7,   8,   7,   4,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 84
    (  0,   5,   7,   5,   2,   0,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 90
    (  0,   4,   4,   2,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 96
    (  0,   2,   2,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 102
    (  0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 108
], dtype=np.float32)

# Piano darkening (velocity < 80): softer felt rounds off upper harmonics smoothly.
SOFT_DELTA_DB = np.array([
    (  2,   1,   0,  -1,  -2,  -4,  -7, -10, -14, -18, -20, -22, -24, -24),  # MIDI 36
    (  1,  -2,  -5,  -8, -11, -15, -18, -20, -20, -18, -16, -16, -16, -16),  # MIDI 60
    (  0,  -4,  -8, -12, -14, -14, -12, -10, -10, -10, -10, -10, -10, -10),  # MIDI 84
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

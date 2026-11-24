"""Register calibration for the procedural piano. All values are scalar parameters."""

import numpy as np


KEYS = np.arange(24, 109, 6)
# Stiff-string coefficients estimated from the Steinway B at the University of Iowa.
INHARMONICITY = np.array([
    .000247, .000127, .000140, .000140, .000114, .000191, .000303, .000591,
    .001094, .001343, .002616, .003381, .0058, .0059, .00896,
])
LATE_LOSS = np.array([
    .39, .80, .46, 1.31, 1.37, 1.55, 2.30, 2.90,
    3.18, 1.74, .85, 1.71, 2.60, 4.40, 6.35,
])
PROMPT_SHARE = np.array([
    .16, .25, .18, .31, .19, .22, .38, .42,
    .53, .72, .86, .74, .47, .62, .65,
])
MODE_BANDS = (1, 2, 3, 4, 5, 7, 10, 14, 20, 29, 42, 62, 90, 133)


# Broad bridge-gain corrections in dB. Each row corresponds to one
# calibration key and each column to the partial group starting in MODE_BANDS.
# Fit to early pressure-band energy at mezzo-forte, plus separate dynamic
# corrections. The reference waveforms are external to the synthesizer.
MEDIUM_DB = np.array([
    (-28, -20, -10, -12,  -8, -12,  -1,  -4,  -8, -17, -23, -22,   1, -15),  # MIDI 24
    (-28,  -8, -10,  -4, -10,  -8,   1,  -6,  -3, -15, -25, -18,   6, -15),  # MIDI 30
    (-18,  -6,  -6, -11,  -4,   1,  -3,  -5,  -9, -11, -23,  -8,  17, -15),  # MIDI 36
    ( -5,   0,  -1, -14,  -6,  -1,  -1, -15, -24, -27, -20,   2, -15, -15),  # MIDI 42
    ( -3,   0,  -4,  -5,  -3,   5,  -2,  -8, -26, -17, -10, -15, -15, -15),  # MIDI 48
    (  1,   1,  -3,  -2,   2,   1, -20, -24, -24, -11,   5, -15, -15, -15),  # MIDI 54
    ( 10,   0,  -5,  -7,  -5, -10, -29, -34, -16,   8, -15, -15, -15, -15),  # MIDI 60
    ( 10,   5,  -5,  -6,  -8, -15, -21, -24,   3, -15, -15, -15, -15, -15),  # MIDI 66
    ( 12, -13, -13, -26, -37, -40, -17, -15, -15, -15, -15, -15, -15, -15),  # MIDI 72
    ( 14,  -3, -21, -25, -26, -31,  -3, -15, -15, -15, -15, -15, -15, -15),  # MIDI 78
    ( 18,  -6, -18, -30, -37, -23, -15, -15, -15, -15, -15, -15, -15, -15),  # MIDI 84
    ( 19, -20, -34, -30, -31, -15, -15, -15, -15, -15, -15, -15, -15, -15),  # MIDI 90
    ( 17,  -9, -22, -22, -15, -15, -15, -15, -15, -15, -15, -15, -15, -15),  # MIDI 96
    ( 21,  10, -17, -15, -15, -15, -15, -15, -15, -15, -15, -15, -15, -15),  # MIDI 102
    ( 25,   8, -15, -15, -15, -15, -15, -15, -15, -15, -15, -15, -15, -15),  # MIDI 108
], dtype=np.float32)

LOUD_DELTA_DB = np.array([
    ( -3,  -4,  -1,  -4,  -2,  -1,  -3,  -1,   3,  10,  21,  24,   6,   0),  # MIDI 24
    ( -1,  -1,  -4,  -3,  -1,  -1,  -3,   2,   2,   8,  15,  16,  -3,   0),  # MIDI 30
    (  1,  -1,  -1,   0,  -2,  -1,   1,   0,   3,   4,  11,   6,  -2,   0),  # MIDI 36
    ( -1,  -3,   0,   1,  -3,   0,   3,   8,  15,  16,   7,  -2,   0,   0),  # MIDI 42
    ( -1,   0,  -1,  -1,  -3,   0,   1,   5,  12,  14,  -1,   0,   0,   0),  # MIDI 48
    ( -2,  -1,  -1,  -2,   0,   3,  10,  11,  17,  -3,  -4,   0,   0,   0),  # MIDI 54
    ( -1,   1,  -3,   2,   3,   7,  20,  24,  13,  -3,   0,   0,   0,   0),  # MIDI 60
    (  0,  -1,  -4,   2,   3,   5,  15,  18,  -3,   0,   0,   0,   0,   0),  # MIDI 66
    (  0,  -1,  10,  14,  18,  19,   4,  -3,   0,   0,   0,   0,   0,   0),  # MIDI 72
    ( -1,   1,   6,   4,   3,  14,  -5,   0,   0,   0,   0,   0,   0,   0),  # MIDI 78
    (  0,   6,  -1,   4,   5,  -5,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 84
    ( -1,  10,  14,   3,  -3,   0,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 90
    ( -1,   6,   0,  -3,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 96
    ( -1,   0,  -1,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 102
    ( -1,  -8,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 108
], dtype=np.float32)

SOFT_DELTA_DB = np.array([
    (  1,   0,   1,   1,   0,   1,  -7,  -9, -21, -14,   0,  10, -32,   0),  # MIDI 36
    (  0,  -7, -11, -11, -20, -29,  -5,   6,   8,   5,   0,   0,   0,   0),  # MIDI 60
    (  0,  12,  -1,   4,  14,  17,   0,   0,   0,   0,   0,   0,   0,   0),  # MIDI 84
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

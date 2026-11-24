"""Procedural pressure radiation from a shared, orthotropic piano soundboard."""

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


@lru_cache(maxsize=128)
def bridge_position(pitch):
    """Return the two nearest bridge points and their interpolation weight."""
    position = np.interp(pitch, BRIDGE_KEYS, np.arange(BRIDGE_CHANNELS))
    left = min(int(position), BRIDGE_CHANNELS - 2)
    return left, np.float32(position - left)


@lru_cache(maxsize=8)
def _plate_design(sample_rate):
    # Thin-plate modal frequencies with different rigidity along/across
    # the grain. Ribs are represented by the cross-axis stiffness term.
    m, n = np.meshgrid(np.arange(1, 12), np.arange(1, 10), indexing="ij")
    long_axis, short_axis = 31.0 * m**2, 18.0 * n**2
    frequency = np.sqrt(long_axis**2 + short_axis**2 + 1.3 * long_axis * short_axis)
    keep = frequency < min(2200.0, 0.42 * sample_rate)
    hz = frequency[keep]
    shapes = np.column_stack((m[keep], n[keep]))

    def mode_shape(points):
        along = np.sin(np.pi * points[:, :1] * shapes[:, 0])
        across = np.sin(np.pi * points[:, 1:] * shapes[:, 1])
        return along * across

    bridge_modes = mode_shape(BRIDGE_POINTS).astype(np.float32)
    # A microphone receives radiation over an area, so fine nodal patterns
    # contribute less in the far field than large-scale plate motion.
    area_average = np.exp(-0.014 * np.sum(shapes**2, axis=1))
    pressure_modes = (mode_shape(MICROPHONE_POINTS).T * area_average[:, None]).astype(np.float32)

    theta = 2 * np.pi * hz / sample_rate
    loss = 17 + 0.044 * hz
    radius = np.exp(-loss / sample_rate)
    # Real part of an impulse-driven damped oscillator models plate
    # velocity. Keep peak gains comparable across modes of different Q.
    leading = (1 - radius) * (1 - np.exp(-hz / 140))
    numerator = np.column_stack((leading, -leading * radius * np.cos(theta))).astype(np.float32)
    denominator = np.column_stack((np.ones(len(hz)), -2 * radius * np.cos(theta), radius**2)).astype(np.float32)

    # Above the sparse modal region many plate modes overlap. Model this
    # part of the transfer as a broadband radiation path from the bridge.
    radiation = butter(
        1, [54, min(9200, 0.44 * sample_rate)], btype="bandpass", fs=sample_rate, output="sos"
    ).astype(np.float32)
    return bridge_modes, pressure_modes, numerator, denominator, radiation


class PianoSoundboard:
    """Bridge force in, stereo sound pressure out, with one state per render."""

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.bridge_modes, self.mode_pressure, self.numerator, self.denominator, self.radiation = _plate_design(sample_rate)
        self.modal_state = np.zeros((len(self.numerator), 2), dtype=np.float32)
        self.radiation_state = np.zeros((len(self.radiation), 2, BRIDGE_CHANNELS), dtype=np.float32)
        self.diffuse_pressure = np.array(
            [[0.79, 0.32], [0.78, 0.49], [0.72, 0.67], [0.52, 0.78], [0.36, 0.79]],
            dtype=np.float32,
        )

    def reset(self):
        self.modal_state.fill(0)
        self.radiation_state.fill(0)

    def process(self, force, resonance):
        radiation, self.radiation_state = sosfilt(
            self.radiation, force, axis=0, zi=self.radiation_state
        )
        stereo = np.einsum("fb,bc->fc", radiation, self.diffuse_pressure)
        modal_force = np.einsum("fb,bm->mf", radiation, self.bridge_modes)
        for index, drive in enumerate(modal_force):
            response, self.modal_state[index] = lfilter(
                self.numerator[index], self.denominator[index], drive, zi=self.modal_state[index]
            )
            modal_force[index] = response
        stereo += np.float32(0.28 + 0.78 * resonance) * np.einsum(
            "mf,mc->fc", modal_force, self.mode_pressure
        )
        return stereo

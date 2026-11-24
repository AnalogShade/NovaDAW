"""Modal-physical synthesis of struck grand piano strings, hammer impact, duplex scale, and room."""
from dataclasses import dataclass
from functools import lru_cache
import math

import numpy as np
from scipy.signal import butter, sosfilt

from plugins.piano.piano_soundboard import PianoSoundboard
from plugins.piano.piano_voicing import (
    INHARMONICITY, KEYS, LATE_LOSS, PROMPT_SHARE, band_indices, modal_voicing,
)
from plugins.synth.synth_dsp import StereoStudioReverb


# Label, delay-length multiplier, feedback control, damping, and tail budget.
ROOMS = {
    "small_room": ("Petite pièce", 0.65, 0.18, 0.55, 0.8),
    "large_room": ("Grande pièce", 0.95, 0.46, 0.46, 1.5),
    "concert_hall": ("Salle de concert", 1.25, 0.73, 0.38, 2.8),
    "cathedral": ("Cathédrale", 1.65, 0.94, 0.40, 5.0),
}
DRY_TAIL_SEC = 1.25
DAMPED_TAIL_SEC = 0.33
BOARD_HISTORY_SEC = 0.18
MODE_BLOCK_FRAMES = 128


def hammer_contact_seconds(register, strike, hardness):
    """Estimate grand-piano hammer-string contact time from a power-law felt spring."""
    # Realistic grand-piano felt contact times (~3.7 ms at A0, ~3.0 ms at C2,
    # ~1.9 ms at C4, ~0.95 ms at C6, ~0.38 ms at C8).
    nominal = 0.00038 + 0.00332 * (1.0 - register) ** 1.40
    stiffness = 0.46 + 1.24 * hardness
    speed = 0.19 + 0.81 * strike
    return nominal * stiffness ** (-1.0 / 3.5) * speed ** (-1.5 / 3.5)


def felt_force_terms(duration):
    # Approximate the force of a single contact by a raised cosine with faster
    # unloading. Divide by its area so strike strength sets momentum independently.
    skew = 2.0
    pulse_decay = -skew / duration
    pulse_omega = 2 * np.pi / duration
    area = duration * (-np.expm1(-skew)) * (1 / skew - skew / (skew**2 + (2 * np.pi)**2))

    rates = pulse_decay + 1j * np.array([0.0, pulse_omega, -pulse_omega])
    return rates, np.array([1.0, -.5, -.5]) / area


def contact_response(time, poles, duration):
    """Causal convolution of a unit-impulse felt force with complex modes."""
    rates, weights = felt_force_terms(duration)
    response = np.zeros_like(time * poles, dtype=np.complex128)
    for rate, weight in zip(rates, weights):
        difference = poles - rate
        response += weight * np.exp(rate * time) * np.expm1(difference * time) / difference
    return response


class HammerDrivenModes:
    """Oscillator states for one finite contact from a position and velocity of zero."""

    def __init__(self, omega, losses, contact_seconds, branch_weights):
        self.duration = contact_seconds
        self.poles = -losses + 1j * omega
        self.displacement_weights = np.asarray(branch_weights)[:, None] / omega

    def displacement_state(self, time):
        driven_time = np.minimum(time, self.duration)
        return (
            contact_response(driven_time, self.poles, self.duration)
            * np.exp(self.poles * (time - driven_time))
            * self.displacement_weights
        )

    def response(self, time):
        state = self.displacement_state(time[:, None, None])
        return np.sum(state.imag, axis=1).astype(np.float32)


@dataclass(frozen=True)
class ModalBank:
    poles: np.ndarray
    kernel: np.ndarray
    kernel_t: np.ndarray


@dataclass(frozen=True)
class LongitudinalModes:
    bank: ModalBank
    left: np.ndarray
    right: np.ndarray
    projection: np.ndarray


@dataclass(frozen=True)
class StringModes(ModalBank):
    bridge_force: np.ndarray
    bands: np.ndarray
    primary_count: int
    ring_count: int
    ring_branches: int
    longitudinal: LongitudinalModes | None


def _modal_bank(poles, sample_rate):
    powers = np.empty((MODE_BLOCK_FRAMES, len(poles)), dtype=np.complex128)
    powers[0] = 1
    step = np.exp(poles / sample_rate)
    size = 1
    while size < MODE_BLOCK_FRAMES:
        count = min(size, MODE_BLOCK_FRAMES - size)
        powers[size:size + count] = powers[:count] * step
        step *= step
        size *= 2
    kernel = powers.astype(np.complex64)
    return ModalBank(poles, kernel, np.ascontiguousarray(kernel.T))


def _longitudinal_modes(pitch, frequency, loss, sample_rate):
    # Second-order string stretching generates a component at twice each
    # transverse mode, and a sum component for neighbouring modes. These
    # phantom partials give copper-wound bass piano strings their bronze authority.
    count = min(22, len(frequency))
    if pitch >= 55 or count < 2:
        return None
    orders = np.arange(count)
    left = np.concatenate((orders, orders[:-1]))
    right = np.concatenate((orders, orders[1:]))
    phantom_hz = frequency[left] + frequency[right]
    keep = phantom_hz < min(4400.0, 0.44 * sample_rate)
    left, right, phantom_hz = left[keep], right[keep], phantom_hz[keep]
    if not len(left):
        return None
    poles = -(loss[left] + loss[right] + 3.0) + 2j * np.pi * phantom_hz
    bank = _modal_bank(np.concatenate((poles, poles - 1 / .008)), sample_rate)
    band = phantom_hz / frequency[0]
    transfer = .38 + .62 / (1 + ((band - 13) / 7.5)**2)
    # Smooth taper across wound-string break so phantom partials sit ~-20 to -26 dB below main tone.
    projection = np.interp(pitch, (21, 34, 46, 54), (0.78, 1.15, 0.42, 0.08)) * transfer
    return LongitudinalModes(bank, left, right, projection)


@lru_cache(maxsize=176)
def string_modes(pitch, sample_rate, board_resonance):
    """Resolve a stiff piano unison (mono/bi/trichord), polarizations, duplex scale, and bridge load."""
    register = np.clip((pitch - 21) / 87, 0.0, 1.0)
    fundamental = 440.0 * 2 ** ((pitch - 69) / 12)
    stiffness = np.exp(np.interp(pitch, KEYS, np.log(INHARMONICITY)))
    partial = np.arange(1, 155, dtype=np.float64)
    frequency = fundamental * partial * np.sqrt((1 + stiffness * partial**2) / (1 + stiffness))
    limit = min(14800.0, sample_rate * 0.45)
    keep = frequency < limit
    partial, frequency = partial[keep], frequency[keep]

    natural_loss = np.interp(pitch, KEYS, LATE_LOSS)
    # Wide, perceptible sustain scaling: 0% gives a short, damped studio piano decay;
    # 67% gives balanced concert-grand sustain; 100% gives lush, singing long sustain (~6x 0%).
    load = float(np.interp(board_resonance, (0.0, 0.35, 0.67, 1.0), (2.65, 1.60, 1.00, 0.44)))
    # Keep internal steel friction + viscous air damping independent of soundboard bridge load
    # so 0% resonance stays crisp/dry rather than artificially muffled.
    internal_air_loss = (
        (0.042 + 0.085 * register) * (partial - 1)
        + 3.6 * (frequency / 2600.0) ** 1.62
    )
    slow_loaded = natural_loss * load + internal_air_loss
    treble_handover = np.interp(pitch, (72, 78, 84, 90, 96, 108), (0.0, 1.4, 3.6, 5.2, 6.2, 7.2))
    prompt_loaded = (
        slow_loaded
        + (4.5 + 3.4 * (frequency / 800.0) ** 0.85 + treble_handover) * (0.65 + 0.35 * load)
    )

    # True multi-string unison & Weinreich two-polarization coupling:
    # - Prompt energy is shared across mistuned vertical unison components so even
    #   the first 150 ms has true piano bichord/trichord ensemble width (never a single guitar wire).
    # - Singing sustain branches carry >=48% of amplitude across 2 (bass) or 3 (mid/treble) strings.
    prompt_share = np.interp(pitch, KEYS, PROMPT_SHARE)
    aftersound = 1.0 - prompt_share
    if pitch < 29:
        # Heavy copper-wound bass monochord/bichord: tight, majestic beating
        branches = [
            (0.0, prompt_share * 0.65, True, 0.0),
            (0.32, prompt_share * 0.35, True, 0.004),
            (-0.36, aftersound * 0.54, False, -0.005),
            (0.44, aftersound * 0.46, False, 0.006),
        ]
    elif pitch < 40:
        # Wound bass bichord
        branches = [
            (0.0, prompt_share * 0.58, True, 0.0),
            (-0.48, prompt_share * 0.42, True, -0.006),
            (-0.62, aftersound * 0.52, False, -0.008),
            (0.74, aftersound * 0.48, False, 0.009),
        ]
    else:
        # Steel trichord (E2 to C8): 2 coupled vertical prompt modes + 3 singing sustain strings
        branches = [
            (0.0, prompt_share * 0.55, True, 0.0),
            (-0.54, prompt_share * 0.45, True, -0.006),
            (-0.82, aftersound * 0.34, False, -0.009),
            (0.14, aftersound * 0.33, False, 0.002),
            (0.94, aftersound * 0.33, False, 0.010),
        ]

    # Parabolic wool-felt hammer crown geometry & compliant bridge impedance transfer:
    # As strings shorten toward treble (~5 cm at C8), felt contact width becomes a larger
    # fraction of string length, naturally rounding off sharp guitar-pick comb zeroes.
    base_strike_point = 0.118 - 0.022 * register
    felt_width = 0.014 + 0.036 * register ** 1.50
    bridge_sign = np.where((partial.astype(np.int64) & 1) == 1, -1.0, 1.0)

    # Physical string-to-soundboard velocity impedance transfer: copper-wound bass strings
    # retain richer upper harmonic ring (~1/n^0.22) while plain treble steel tapers (~1/n^0.50).
    partial_slope = 0.22 + 0.28 * register
    impedance_transfer = (
        partial * np.sqrt((1 + stiffness * partial**2) / (1 + stiffness))
        / ((partial ** partial_slope) * np.sqrt(1.0 + (frequency / 4800.0) ** 2))
    )
    # Smooth register loudness regulation compensating for partial density across 88 keys.
    register_regulation = np.interp(
        pitch,
        (21, 33, 45, 57, 69, 81, 93, 108),
        (0.66, 0.80, 1.08, 1.40, 1.78, 2.28, 2.95, 3.85),
    )

    unison_rate = 0.88 + 0.12 * np.cos(0.91 * pitch + 0.67 * partial)
    unison_rate *= min(1.0, 480.0 / fundamental)

    angular_frequencies, losses, forces, bands = [], [], [], []
    ring = (partial == 1) | (frequency < min(8200.0, limit))
    for index, (cents, share, is_prompt, sp_off) in enumerate(branches):
        is_primary = (index == 0)
        selected = np.ones_like(frequency, dtype=bool) if is_primary else ring
        modal_share = np.where(ring, share, prompt_share) if is_primary else share
        sp = base_strike_point + sp_off
        raw_sin = np.sin(np.pi * partial * sp)
        spatial_sign = np.where(raw_sin >= 0.0, 1.0, -1.0)
        # Distributed felt patch + compliant termination fills artificial nodal comb zeroes
        # while parabolic felt profile rolls off high partials without rectangular sinc sidelobes.
        spatial_mag = np.sqrt(raw_sin ** 2 + (0.24 * np.pi * partial * felt_width) ** 2)
        crown_envelope = 1.0 / (1.0 + (partial * felt_width * 1.15) ** 2)
        projection = bridge_sign * spatial_sign * spatial_mag * crown_envelope

        force = 2 * np.pi * fundamental * register_regulation * impedance_transfer * projection
        detuned = frequency[selected] * 2 ** (cents * unison_rate[selected] / 1200)
        omega = 2 * np.pi * detuned
        angular_frequencies.append(omega)
        losses.append((prompt_loaded if is_prompt else slow_loaded)[selected])
        forces.append((force * modal_share)[selected])
        bands.append(band_indices(partial[selected]))

    ring_branches = len(branches) - 1

    # Steinway duplex scaling (sympathetic aliquot overtones in treble, pitch >= 58):
    # adds crystalline bell halo at 2*f0 and 3*f0 that no guitar string possesses.
    if pitch >= 58:
        duplex_ratios = np.array([2.0018, 3.0035], dtype=np.float64)
        duplex_hz = fundamental * duplex_ratios
        keep_dup = duplex_hz < limit
        if np.any(keep_dup):
            dup_hz = duplex_hz[keep_dup]
            dup_omega = 2 * np.pi * dup_hz
            dup_loss = (2.1 + 0.0016 * dup_hz) * load
            dup_scale = float(np.interp(board_resonance, (0.0, 0.67, 1.0), (0.15, 1.00, 1.95)))
            dup_level = np.interp(pitch, (58, 72, 88, 108), (0.018, 0.036, 0.052, 0.060)) * dup_scale
            dup_force = 2 * np.pi * fundamental * register_regulation * dup_level * np.array([1.0, -0.65])[:len(dup_hz)]
            angular_frequencies.append(dup_omega)
            losses.append(dup_loss)
            forces.append(dup_force)
            bands.append(band_indices(np.array([2, 3])[:len(dup_hz)]))

    poles = -np.concatenate(losses) + 1j * np.concatenate(angular_frequencies)
    bank = _modal_bank(poles, sample_rate)
    longitudinal = _longitudinal_modes(pitch, frequency, slow_loaded, sample_rate)
    return StringModes(
        bank.poles, bank.kernel, bank.kernel_t, np.concatenate(forces), np.concatenate(bands),
        len(frequency), int(np.count_nonzero(ring)), ring_branches, longitudinal,
    )


@dataclass(frozen=True)
class BridgeStrike:
    contact: np.ndarray
    separation_state: np.ndarray
    longitudinal_state: np.ndarray | None
    thump: np.ndarray


def _project_modes(output, modes, state, elapsed, sample_rate):
    """Sum forward from an oscillator state using vectorized BLAS matrix blocks."""
    total = len(output)
    if total == 0:
        return
    n_full = total // MODE_BLOCK_FRAMES
    if n_full > 0:
        ages = elapsed + (np.arange(n_full, dtype=np.float64) * MODE_BLOCK_FRAMES) / sample_rate
        positions = (state[None, :] * np.exp(ages[:, None] * modes.poles[None, :])).astype(np.complex64)
        blocks = positions @ modes.kernel_t
        output[:n_full * MODE_BLOCK_FRAMES] = blocks.imag.ravel()
    rem = total - n_full * MODE_BLOCK_FRAMES
    if rem > 0:
        age = elapsed + (n_full * MODE_BLOCK_FRAMES) / sample_rate
        position = (state * np.exp(age * modes.poles)).astype(np.complex64)
        output[n_full * MODE_BLOCK_FRAMES:] = (modes.kernel[:rem] @ position).imag


def _pulse_coefficients(poles, projection, duration):
    # The complex convolution has one recurrence per string mode and
    # three force terms common to all modes. Keep their residues separately.
    rates, weights = felt_force_terms(duration)
    residues = projection[:, None] * (weights / (poles[:, None] - rates))
    modal = residues.sum(axis=1)
    terminal = modal * np.exp(duration * poles) - residues @ np.exp(duration * rates)
    return rates, residues.sum(axis=0), modal, terminal


def _hammer_at_bridge(modes, drive, duration, core_share, sample_rate):
    core_duration = .29 * duration
    core_frames = math.ceil(core_duration * sample_rate)
    contact_frames = math.ceil(duration * sample_rate)
    projection = drive / modes.poles.imag
    slow_rates, slow_terms, slow_modal, slow_end = _pulse_coefficients(
        modes.poles, projection * (1 - core_share), duration,
    )
    core_rates, core_terms, core_modal, core_end = _pulse_coefficients(
        modes.poles, projection * core_share, core_duration,
    )
    force = np.empty(contact_frames, dtype=np.float32)
    _project_modes(force[:core_frames], modes, slow_modal + core_modal, 0.0, sample_rate)
    start = core_frames / sample_rate
    state = slow_modal * np.exp(start * modes.poles) + core_end * np.exp((start - core_duration) * modes.poles)
    _project_modes(force[core_frames:], modes, state, 0.0, sample_rate)

    time = np.arange(contact_frames) / sample_rate
    force -= np.einsum("ft,t->f", np.exp(time[:, None] * slow_rates), slow_terms).imag
    force[:core_frames] -= np.einsum(
        "ft,t->f", np.exp(time[:core_frames, None] * core_rates), core_terms
    ).imag
    force[0] = 0
    end = contact_frames / sample_rate
    separation = (
        slow_end * np.exp((end - duration) * modes.poles)
        + core_end * np.exp((end - core_duration) * modes.poles)
    )
    return force, separation


def _hammer_thump(pitch, velocity, sample_rate, hardness):
    """Synthesize subtle, non-pitched mechanical keybed mass and wool-felt impact brush."""
    register = np.clip((pitch - 21) / 87, 0.0, 1.0)
    strike = velocity / 127.0
    frames = max(64, round(0.032 * sample_rate))
    t = np.arange(frames, dtype=np.float64) / sample_rate
    rng = np.random.default_rng(10007 * pitch + 313)

    # 1. Aperiodic low-frequency keybed/rim felt mass (42-155 Hz broadband impulse).
    #    Zero pitched sine oscillators so it never sounds like tapping a hollow guitar box.
    thud_noise = rng.standard_normal(frames)
    thud_sos = butter(
        2, [min(42.0, 0.12 * sample_rate), min(145.0 + 45.0 * register, 0.30 * sample_rate)],
        btype="bandpass", fs=sample_rate, output="sos",
    )
    thud_filtered = sosfilt(thud_sos, thud_noise)
    thud_filtered /= np.sqrt(np.mean(thud_filtered**2) + 1e-12)
    thud_env = (1.0 - np.exp(-t / 0.0022)) * np.exp(-t / (0.009 + 0.005 * (1.0 - register)))
    thud_amp = (0.048 + 0.055 * (1.0 - register)) * (strike ** 1.40)
    wood = thud_filtered * thud_env * thud_amp

    # 2. Compressed wool-felt hammer-crown brush (short 2.5-4.5 ms velvet transient).
    noise = rng.standard_normal(frames)
    low_hz = min(620.0 + 780.0 * register + 380.0 * hardness, 0.28 * sample_rate)
    high_hz = min(1950.0 + 1850.0 * register + 1450.0 * hardness, 0.42 * sample_rate)
    if high_hz <= low_hz + 80.0:
        high_hz = min(low_hz + 150.0, 0.44 * sample_rate)
    sos = butter(2, [low_hz, high_hz], btype="bandpass", fs=sample_rate, output="sos")
    felt_noise = sosfilt(sos, noise)
    felt_noise /= np.sqrt(np.mean(felt_noise**2) + 1e-12)

    knock_decay = 0.0026 + 0.0020 * (1.0 - register) * (1.0 - 0.35 * hardness)
    knock_env = (1.0 - np.exp(-t / 0.0006)) * np.exp(-t / knock_decay)
    knock_amp = (0.018 + 0.044 * hardness) * (0.65 + 0.55 * register) * (strike ** 1.70)
    knock = felt_noise * knock_env * knock_amp

    fade = np.clip((frames - 1 - np.arange(frames)) / max(1, round(0.004 * sample_rate)), 0.0, 1.0)
    out = (wood + knock) * fade
    out[0] = 0.0
    return out.astype(np.float32)


@lru_cache(maxsize=512)
def hammer_strike(pitch, velocity, sample_rate, hardness, board_resonance):
    modes = string_modes(pitch, sample_rate, board_resonance)
    register = np.clip((pitch - 21) / 87, 0.0, 1.0)
    strike = velocity / 127.0
    duration = hammer_contact_seconds(register, strike, hardness)
    # Inner compressed wool-felt core carries concert-grand upper-harmonic sheen on forte
    # strikes and copper-wound bass definition while remaining plush at soft dynamics.
    core_share = float(np.clip(
        (0.22 - 0.04 * register) * (0.38 + 0.62 * strike)
        + 0.26 * (hardness - 0.35) * (0.35 + 0.65 * strike),
        0.03, 0.45,
    ))
    drive = (.88 * strike**1.52) * modes.bridge_force
    drive *= modal_voicing(pitch, velocity, modes.bands)

    # Clean, comb-free analytical hammer-string contact integration.
    contact, separation = _hammer_at_bridge(modes, drive, duration, core_share, sample_rate)

    longitudinal_state = None
    longitudinal = modes.longitudinal
    if longitudinal is not None:
        transverse = separation[:modes.primary_count].copy()
        if modes.ring_count and modes.ring_branches:
            ring_end = modes.primary_count + modes.ring_branches * modes.ring_count
            transverse[:modes.ring_count] += (
                separation[modes.primary_count:ring_end]
                .reshape(modes.ring_branches, modes.ring_count)
                .sum(axis=0)
            )
        strain = -.5j * longitudinal.projection * transverse[longitudinal.left] * transverse[longitudinal.right]
        longitudinal_state = np.concatenate((strain, -strain))

    thump = _hammer_thump(pitch, velocity, sample_rate, hardness)
    return BridgeStrike(contact, separation, longitudinal_state, thump)


def render_bridge_force(pitch, velocity, first_sample, frames, held_samples, sample_rate,
                        hardness, board_resonance):
    """Render the force at a bridge location using an absolute note clock."""
    modes = string_modes(pitch, sample_rate, board_resonance)
    strike = hammer_strike(pitch, velocity, sample_rate, hardness, board_resonance)
    separation = len(strike.contact)
    out = np.empty(frames, dtype=np.float32)
    driven = min(frames, max(0, separation - first_sample))
    out[:driven] = strike.contact[first_sample:first_sample + driven]

    age = (first_sample + driven - separation) / sample_rate
    _project_modes(out[driven:], modes, strike.separation_state, age, sample_rate)
    if strike.longitudinal_state is not None and driven < frames:
        strain = np.empty(frames - driven, dtype=np.float32)
        _project_modes(strain, modes.longitudinal.bank, strike.longitudinal_state, age, sample_rate)
        out[driven:] += strain

    # Add subtle non-pitched mechanical action/felt transient (~32 ms).
    thump_len = len(strike.thump)
    if first_sample < thump_len:
        thump_n = min(frames, thump_len - first_sample)
        out[:thump_n] += strike.thump[first_sample:first_sample + thump_n]

    if first_sample + frames > held_samples:
        after = np.maximum(np.arange(first_sample, first_sample + frames) - held_samples, 0)
        released = after.astype(np.float32) / sample_rate
        if pitch < 90:
            # Lowering the felt damper takes a few milliseconds, then rapidly
            # dissipates the transverse and longitudinal motion of the string.
            contact_time = .012
            load = released + contact_time * np.expm1(-released / contact_time)
            out *= np.exp(-(40.0 + .21 * (pitch - 21)) * load)
            tail = DAMPED_TAIL_SEC
        else:
            tail = DRY_TAIL_SEC
        out *= np.clip((tail - released) / .05, 0, 1)
    return out


class PianoRenderContext:
    """Independent plate and room history for live, sequence and offline rendering."""

    def __init__(self):
        self.next_sample = None
        self.soundboard = None
        self.room = PianoRoom()

    def discard_history(self):
        self.next_sample = None
        self.room.next_sample = None

    def start(self, first_sample, sample_rate):
        if self.soundboard is None or self.soundboard.sample_rate != sample_rate:
            self.soundboard = PianoSoundboard(sample_rate)
            self.next_sample = None
        if self.next_sample != first_sample:
            self.soundboard.reset()
            return True
        return False

    def finish(self, bridge, first_sample, lookback, sample_rate, state):
        pressure = self.soundboard.process(bridge, state["soundboard_resonance"])[lookback:]
        self.next_sample = first_sample + len(pressure)
        return self.room.process(
            pressure, first_sample, sample_rate, state["reverb_room"], state["reverb_mix"]
        )


class PianoRoom:
    """Keep delay history local to a playback, live-keyboard or offline render."""

    def __init__(self):
        self.configuration = None
        self.next_sample = None
        self.reverb = None

    def process(self, stereo, first_sample, sample_rate, room, mix):
        if mix == 0:
            self.configuration = self.next_sample = self.reverb = None
            return stereo
        config = (sample_rate, room)
        if config != self.configuration:
            _, scale, feedback, damping, _ = ROOMS[room]
            self.reverb = StereoStudioReverb(round(sample_rate * scale))
            self.reverb.room_size = feedback
            self.reverb.damping = damping
            self.configuration = config
        elif self.next_sample != first_sample:
            self.reverb.reset()
        self.reverb.mix = mix
        self.next_sample = first_sample + len(stereo)
        return self.reverb.process(stereo)

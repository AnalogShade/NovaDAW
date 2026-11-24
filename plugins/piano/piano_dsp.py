"""Modal-physical synthesis of struck grand piano strings, hammer impact, and room."""
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
    # ~1.9 ms at C4, ~0.95 ms at C6, ~0.38 ms at C8), keeping bass felt round
    # while preserving clear concert-grand upper partials.
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
    return ModalBank(poles, powers.astype(np.complex64))


def _longitudinal_modes(pitch, frequency, loss, sample_rate):
    # Second-order string stretching generates a component at twice each
    # transverse mode, and a sum component for neighbouring modes. These
    # phantom partials give bass piano strings their metallic copper-wound warmth.
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
    poles = -(loss[left] + loss[right] + 2.8) + 2j * np.pi * phantom_hz
    bank = _modal_bank(np.concatenate((poles, poles - 1 / .008)), sample_rate)
    band = phantom_hz / frequency[0]
    transfer = .40 + .60 / (1 + ((band - 14) / 7.5)**2)
    # Calibrated so phantom partials sit ~-16 to -22 dB below transverse energy.
    projection = np.interp(pitch, (21, 36, 54), (1.05, 1.65, 0.22)) * transfer
    return LongitudinalModes(bank, left, right, projection)


@lru_cache(maxsize=176)
def string_modes(pitch, sample_rate, board_resonance):
    """Resolve a stiff piano unison (mono/bi/trichord), polarizations, and bridge load."""
    register = np.clip((pitch - 21) / 87, 0.0, 1.0)
    fundamental = 440.0 * 2 ** ((pitch - 69) / 12)
    stiffness = np.exp(np.interp(pitch, KEYS, np.log(INHARMONICITY)))
    partial = np.arange(1, 145, dtype=np.float64)
    frequency = fundamental * partial * np.sqrt((1 + stiffness * partial**2) / (1 + stiffness))
    limit = min(13500.0, sample_rate * 0.44)
    keep = frequency < limit
    partial, frequency = partial[keep], frequency[keep]

    natural_loss = np.interp(pitch, KEYS, LATE_LOSS)
    # Frequency-dependent internal friction + viscous air damping ensures upper
    # harmonics bloom brightly at attack and melt into warm lower partials.
    slow = (
        natural_loss
        + (0.054 + 0.110 * register) * (partial - 1)
        + 4.9 * (frequency / 2300.0) ** 1.65
    )
    treble_handover = np.interp(pitch, (72, 78, 84, 90, 96, 108), (0, 2, 8, 9, 7, 8))
    prompt = slow + 6.0 + 4.8 * (frequency / 720.0) ** 0.88 + treble_handover
    load = 1.0 - 0.55 * (board_resonance - 0.67)

    # Coupled unison motion (Weinreich): the symmetric prompt branch carries the
    # strong percussive onset and drains fast; the detuned/orthogonal branches
    # carry the singing aftersound with organic bichord/trichord beating.
    prompt_share = np.interp(pitch, KEYS, PROMPT_SHARE)
    aftersound = 1.0 - prompt_share
    # (detune_cents, aftersound_weight, strike_point_offset)
    if pitch < 29:
        side = ((-0.42, 0.56, -0.005), (0.52, 0.44, 0.006))
    elif pitch < 43:
        side = ((-0.76, 0.52, -0.007), (0.94, 0.48, 0.008))
    else:
        side = (
            (-0.88, 0.34, -0.008),
            (0.18, 0.33, 0.002),
            (1.06, 0.33, 0.009),
        )
    branches = [(0.0, prompt_share, True, 0.0)] + [
        (cents, share * aftersound, False, sp_off)
        for cents, share, sp_off in side
    ]

    # Hammer strikes near the agraffe (x_h ≈ 0.118 L); the soundboard bridge is
    # at the opposite end (x = L), introducing the alternating (-1)^n modal sign.
    # Hammer crown width as a fraction of string length grows gently toward treble.
    base_strike_point = 0.118 - 0.024 * register
    felt_width = 0.0085 + 0.014 * register ** 1.35
    bridge_sign = np.where((partial.astype(np.int64) & 1) == 1, -1.0, 1.0)
    shear = partial * (1 + stiffness * partial**2) / (1 + stiffness)

    unison_rate = 0.88 + 0.12 * np.cos(0.91 * pitch + 0.67 * partial)
    unison_rate *= min(1.0, 520.0 / fundamental)

    angular_frequencies, losses, forces, bands = [], [], [], []
    ring = (partial == 1) | (frequency < min(4200.0, limit))
    for cents, share, is_prompt, sp_off in branches:
        selected = np.ones_like(frequency, dtype=bool) if is_prompt else ring
        modal_share = np.where(ring, share, 1.0) if is_prompt else share
        sp = base_strike_point + sp_off
        # Slight strike-zone averaging softens unnatural single-point guitar comb notches.
        projection = (
            bridge_sign
            * np.sin(np.pi * partial * sp)
            * np.sinc(partial * felt_width)
        )
        force = 2 * np.pi * fundamental * shear * projection
        detuned = frequency[selected] * 2 ** (cents * unison_rate[selected] / 1200)
        omega = 2 * np.pi * detuned
        angular_frequencies.append(omega)
        losses.append((prompt if is_prompt else slow)[selected] * load)
        forces.append((force * modal_share)[selected])
        bands.append(band_indices(partial[selected]))

    poles = -np.concatenate(losses) + 1j * np.concatenate(angular_frequencies)
    bank = _modal_bank(poles, sample_rate)
    longitudinal = _longitudinal_modes(pitch, frequency, slow * load, sample_rate)
    return StringModes(
        bank.poles, bank.kernel, np.concatenate(forces), np.concatenate(bands),
        len(frequency), int(np.count_nonzero(ring)), longitudinal,
    )


@dataclass(frozen=True)
class BridgeStrike:
    contact: np.ndarray
    separation_state: np.ndarray
    longitudinal_state: np.ndarray | None
    thump: np.ndarray


def _project_modes(output, modes, state, elapsed, sample_rate):
    """Sum forward from an oscillator state, in cache-sized blocks."""
    for offset in range(0, len(output), MODE_BLOCK_FRAMES):
        size = min(MODE_BLOCK_FRAMES, len(output) - offset)
        age = elapsed + offset / sample_rate
        position = (state * np.exp(age * modes.poles)).astype(np.complex64)
        force = np.einsum("fm,m->f", modes.kernel[:size], position)
        output[offset:offset + size] = force.imag


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
    """Synthesize deterministic percussive hammer-felt + woody soundboard knock."""
    register = np.clip((pitch - 21) / 87, 0.0, 1.0)
    strike = velocity / 127.0
    frames = max(64, round(0.042 * sample_rate))
    t = np.arange(frames, dtype=np.float64) / sample_rate
    rng = np.random.default_rng(10007 * pitch + 313)

    # 1. Broadband wooden keybed / bridge-to-soundboard impact thud (55-360 Hz).
    #    Filtered impact noise + damped low spruce resonances give realistic
    #    percussive hammer mass (never a tonal sine ping or guitar pluck).
    thud_noise = rng.standard_normal(frames)
    thud_sos = butter(
        2, [min(55.0, 0.15 * sample_rate), min(340.0 + 140.0 * register, 0.35 * sample_rate)],
        btype="bandpass", fs=sample_rate, output="sos",
    )
    thud_filtered = sosfilt(thud_sos, thud_noise)
    thud_filtered /= np.sqrt(np.mean(thud_filtered**2) + 1e-12)
    thud_env = (1.0 - np.exp(-t / 0.0010)) * np.exp(-t / (0.011 + 0.007 * (1.0 - register)))
    thud_amp = (0.20 + 0.24 * (1.0 - register)) * (strike ** 1.35)
    f_shift = 1.0 + 0.15 * register
    wood_body = (
        0.55 * np.sin(2 * np.pi * (86.0 * f_shift) * t)
        + 0.30 * np.sin(2 * np.pi * (162.0 * f_shift) * t + 0.8)
        + 0.15 * np.sin(2 * np.pi * (255.0 * f_shift) * t + 1.6)
    )
    wood = (0.68 * thud_filtered + 0.32 * wood_body) * thud_env * thud_amp

    # 2. Felt-on-string / mechanical action knock (band-limited deterministic noise).
    #    Sharp 4-8 ms felt impact shifting brighter with velocity, register, and hardness.
    noise = rng.standard_normal(frames)
    low_hz = min(750.0 + 950.0 * register + 420.0 * hardness, 0.30 * sample_rate)
    high_hz = min(2300.0 + 2200.0 * register + 1650.0 * hardness, 0.42 * sample_rate)
    if high_hz <= low_hz + 80.0:
        high_hz = min(low_hz + 150.0, 0.44 * sample_rate)
    sos = butter(2, [low_hz, high_hz], btype="bandpass", fs=sample_rate, output="sos")
    felt_noise = sosfilt(sos, noise)
    felt_noise /= np.sqrt(np.mean(felt_noise**2) + 1e-12)

    knock_decay = 0.0036 + 0.0032 * (1.0 - register) * (1.0 - 0.35 * hardness)
    knock_env = (1.0 - np.exp(-t / 0.0005)) * np.exp(-t / knock_decay)
    knock_amp = (0.050 + 0.095 * hardness) * (0.72 + 0.50 * register) * (strike ** 1.65)
    knock = felt_noise * knock_env * knock_amp

    # Smooth window to exact zero at boundaries.
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
    # Inner compressed felt core carries the concert-grand upper-harmonic sheen
    # while remaining soft enough in bass/piano dynamics to avoid a pluck spike.
    core_share = float(np.clip(
        (0.11 + 0.14 * register) * (0.45 + 0.55 * strike)
        + 0.22 * (hardness - 0.35) * (0.4 + 0.6 * strike),
        0.02, 0.38,
    ))
    drive = (.85 * strike**1.55) * modes.bridge_force
    drive *= modal_voicing(pitch, velocity, modes.bands)

    # Primary hammer-string contact pulse.
    contact_1, sep_1 = _hammer_at_bridge(modes, drive, duration, core_share, sample_rate)

    # Secondary felt re-compression lobe from the agraffe wave reflection during
    # hammer contact (broadens and rounds the bass/low-mid attack physically).
    fundamental = 440.0 * 2 ** ((pitch - 69) / 12)
    strike_point = 0.118 - 0.024 * register
    refl_delay = min(0.55 * duration, 2.0 * strike_point / fundamental)
    refl_weight = 0.24 * (1.0 - 0.65 * register)
    if refl_weight > 0.03 and refl_delay > 1.5 / sample_rate:
        delay_frames = round(refl_delay * sample_rate)
        refl_dur = 0.72 * duration
        c2, sep_2 = _hammer_at_bridge(
            modes, drive * refl_weight, refl_dur, core_share * 0.5, sample_rate
        )
        total_frames = max(len(contact_1), delay_frames + len(c2))
        contact = np.zeros(total_frames, dtype=np.float32)
        contact[:len(contact_1)] += contact_1
        contact[delay_frames:delay_frames + len(c2)] += c2
        end_t = total_frames / sample_rate
        t1 = len(contact_1) / sample_rate
        t2 = (delay_frames + len(c2)) / sample_rate
        separation = (
            sep_1 * np.exp((end_t - t1) * modes.poles)
            + sep_2 * np.exp((end_t - t2) * modes.poles)
        ) / (1.0 + refl_weight)
        contact /= np.float32(1.0 + refl_weight)
    else:
        contact, separation = contact_1, sep_1

    longitudinal_state = None
    longitudinal = modes.longitudinal
    if longitudinal is not None:
        transverse = separation[:modes.primary_count].copy()
        if modes.ring_count:
            transverse[:modes.ring_count] += separation[modes.primary_count:].reshape(-1, modes.ring_count).sum(axis=0)
        # The product of two displacement responses contains the phase sum.
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

    # Add deterministic percussive hammer/keybed impact transient (first ~36 ms).
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

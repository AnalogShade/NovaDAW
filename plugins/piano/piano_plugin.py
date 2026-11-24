"""Nova Piano, a sample-free grand piano instrument for NovaDAW."""
import math
from threading import RLock
from types import SimpleNamespace

import numpy as np

from plugins.base import BasePlugin
from plugins.piano.piano_dsp import (
    BOARD_HISTORY_SEC,
    DAMPED_TAIL_SEC,
    DRY_TAIL_SEC,
    ROOMS,
    PianoRenderContext,
    render_bridge_force,
)
from plugins.piano.piano_soundboard import BRIDGE_CHANNELS, bridge_position
from plugins.registry import register_plugin


PIANO_ID = "novadaw.piano"
PIANO_NAME = "Nova Piano"
DESCRIPTION = "Piano à queue par synthèse modale, avec réverbération stéréo."


@register_plugin(
    plugin_type_id=PIANO_ID,
    name=PIANO_NAME,
    category="instrument",
    icon="🎹",
    description=DESCRIPTION,
    is_default=True,
)
class NovaPianoPlugin(BasePlugin):
    is_instrument = True
    VOICING = "Piano à queue"
    DEFAULTS = {
        "master_volume": 0.85,
        "reverb_mix": 0.20,
        "reverb_room": "large_room",
        "hammer_hardness": 0.58,
        "soundboard_resonance": 0.67,
    }
    PARAMETER_LIMITS = {
        "master_volume": 2.0,
        "reverb_mix": 1.0,
        "hammer_hardness": 1.0,
        "soundboard_resonance": 1.0,
    }

    def __init__(self, instance_id=None):
        super().__init__(PIANO_ID, PIANO_NAME, "instrument", "🎹", instance_id)
        self.description = DESCRIPTION
        self._state_lock = RLock()
        self._parameters = self.DEFAULTS.copy()
        self._timeline_room = self.create_isolated_context()

    def process(self, audio, sample_rate):
        return audio

    def reset(self):
        self._timeline_room = self.create_isolated_context()

    def get_factory_presets(self):
        return {self.VOICING: self.DEFAULTS.copy()}

    def apply_preset(self, preset_name):
        if preset_name.casefold() == self.VOICING.casefold():
            self.set_state(self.DEFAULTS)
            return True
        from core.preset_manager import global_preset_manager

        return global_preset_manager.load_preset(self, preset_name)

    def get_state(self):
        with self._state_lock:
            return {**self._parameters, "enabled": self.enabled}

    def set_state(self, state):
        with self._state_lock:
            updated = self._parameters.copy()
            for key, maximum in self.PARAMETER_LIMITS.items():
                value = state.get(key, updated[key])
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(number):
                    updated[key] = max(0.0, min(maximum, number))
            room = state.get("reverb_room")
            if isinstance(room, str) and room in ROOMS:
                updated["reverb_room"] = room
            self._parameters = updated
            self.enabled = bool(state.get("enabled", self.enabled))

    @staticmethod
    def _tail_sec(state):
        extra_board = max(0.0, (state["soundboard_resonance"] - 0.67) * 2.2)
        return DRY_TAIL_SEC + extra_board + (ROOMS[state["reverb_room"]][-1] if state["reverb_mix"] else 0)

    def get_release_tail_sec(self):
        return self._tail_sec(self.get_state())

    def create_isolated_context(self, sample_rate=44100):
        return PianoRenderContext()

    def render_slice(self, notes, start_b, end_b, beats_per_sec, frames, sample_rate, bus_index=None):
        return self.render_in_context(
            notes, start_b, beats_per_sec, frames, sample_rate, self._timeline_room
        )

    def render_in_context(self, notes, start_b, beats_per_sec, frames, sample_rate, room, state=None):
        state = self.get_state() if state is None else state
        if not state["enabled"]:
            room.discard_history()
        if not state["enabled"] or frames <= 0 or beats_per_sec <= 0:
            return np.zeros((max(0, frames), 2), dtype=np.float32)

        samples_per_beat = sample_rate / beats_per_sec
        first_sample = round(start_b * samples_per_beat)
        events = []
        for note in notes:
            velocity = min(127, int(note.velocity))
            if velocity <= 0:
                continue
            onset = round(note.start_beat * samples_per_beat)
            held = max(1, round((note.start_beat + note.duration) * samples_per_beat) - onset)
            events.append((max(0, min(127, int(note.pitch))), velocity, onset, held))

        lookback = 0
        if room.start(first_sample, sample_rate) and events:
            earliest = min(event[2] for event in events)
            lookback = min(round(BOARD_HISTORY_SEC * sample_rate), max(0, first_sample - earliest))
        bridge_start = first_sample - lookback
        bridge = np.zeros((frames + lookback, BRIDGE_CHANNELS), dtype=np.float32)
        for pitch, velocity, onset, held in events:
            tail = DAMPED_TAIL_SEC if pitch < 90 else DRY_TAIL_SEC
            offset = max(0, onset - bridge_start)
            end = min(len(bridge), onset + held + round(tail * sample_rate) - bridge_start)
            if end <= offset:
                continue
            force = render_bridge_force(
                pitch, velocity, bridge_start + offset - onset, end - offset, held, sample_rate,
                state["hammer_hardness"], state["soundboard_resonance"],
            )
            left, right_mix = bridge_position(pitch)
            bridge[offset:end, left] += force * (1 - right_mix)
            bridge[offset:end, left + 1] += force * right_mix

        stereo = room.finish(bridge, first_sample, lookback, sample_rate, state)
        return stereo * np.float32(state["master_volume"])

    def render_note(self, pitch, duration_sec=0.7, sample_rate=44100, velocity=100, bus_index=None):
        state = self.get_state()
        duration_sec = max(0.01, duration_sec)
        frames = math.ceil((duration_sec + self._tail_sec(state)) * sample_rate)
        note = SimpleNamespace(pitch=pitch, velocity=velocity, start_beat=0.0, duration=duration_sec)
        return self.render_in_context(
            [note], 0.0, 1.0, frames, sample_rate, self.create_isolated_context(sample_rate), state
        )

    def create_editor(self, parent=None):
        from plugins.piano.piano_gui import NovaPianoEditor

        return NovaPianoEditor(self, parent)

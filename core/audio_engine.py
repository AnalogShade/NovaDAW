"""
core/audio_engine.py - Moteur audio temps réel, synthétiseur polyphonique et mixeur
"""
import threading
import time
from typing import Optional, List, Dict, Callable
import numpy as np
import sounddevice as sd
import soundfile as sf
from core.project import Project, Track, MidiClip, AudioClip, MidiNote


def midi_to_freq(pitch: int) -> float:
    """Convertit un numéro de note MIDI en fréquence en Hertz"""
    return 440.0 * (2.0 ** ((pitch - 69.0) / 12.0))


class SynthVoice:
    """Générateur de timbre synthétiseur avec enveloppe ADSR douce"""
    @staticmethod
    def generate_note(pitch: int, duration_sec: float, sample_rate: int = 44100, velocity: int = 100) -> np.ndarray:
        freq = midi_to_freq(pitch)
        num_samples = max(1, int(sample_rate * duration_sec))
        t = np.linspace(0, duration_sec, num_samples, endpoint=False)

        # Synthèse soustractive / additive chaude : Fondamentale + harmoniques
        # Mélange Sinus + Scie atténuée pour un son riche et doux
        sine = np.sin(2.0 * np.pi * freq * t)
        saw = 0.5 * (2.0 * (t * freq - np.floor(0.5 + t * freq)))
        sub = 0.3 * np.sin(2.0 * np.pi * (freq * 0.5) * t)
        signal = 0.6 * sine + 0.3 * saw + 0.2 * sub

        # Enveloppe ADSR (Attack: 10ms, Decay: 50ms, Sustain: 70%, Release: 40ms)
        envelope = np.ones(num_samples, dtype=np.float32)
        attack_len = min(num_samples // 4, int(sample_rate * 0.012))
        decay_len = min(num_samples // 4, int(sample_rate * 0.060))
        release_len = min(num_samples // 3, int(sample_rate * 0.040))

        if attack_len > 0:
            envelope[:attack_len] = np.linspace(0.0, 1.0, attack_len)
        if decay_len > 0 and attack_len + decay_len < num_samples:
            envelope[attack_len:attack_len + decay_len] = np.linspace(1.0, 0.75, decay_len)
            envelope[attack_len + decay_len:] = 0.75
        if release_len > 0 and num_samples > release_len:
            envelope[-release_len:] = np.linspace(0.75, 0.0, release_len)

        # Application vélocité et enveloppe
        vel_scale = (velocity / 127.0) * 0.4
        voice_mono = (signal * envelope * vel_scale).astype(np.float32)
        # Stéréo
        return np.column_stack((voice_mono, voice_mono))


class AudioEngine:
    def __init__(self, sample_rate: int = 44100, block_size: int = 1024):
        self.sample_rate = sample_rate
        self.block_size = block_size
        self.project: Optional[Project] = None

        self.is_playing = False
        self.current_beat = 0.0
        self.master_volume = 0.9

        # File d'attente pour les notes de prévisualisation (clic sur piano roll)
        self._preview_lock = threading.Lock()
        self._preview_buffers: List[Dict] = []

        # Tête de lecture et notification UI
        self.playhead_callback: Optional[Callable[[float], None]] = None
        self._stream: Optional[sd.OutputStream] = None

        self._start_stream()

    def set_project(self, project: Project):
        self.project = project
        self.current_beat = 0.0

    def _start_stream(self):
        try:
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                blocksize=self.block_size,
                channels=2,
                dtype="float32",
                callback=self._audio_callback
            )
            self._stream.start()
        except Exception as e:
            print(f"[AudioEngine] Erreur initialisation flux audio: {e}")
            self._stream = None

    def close(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def play(self):
        self.is_playing = True

    def pause(self):
        self.is_playing = False

    def stop(self):
        self.is_playing = False
        if self.project and self.project.loop_enabled:
            self.current_beat = self.project.loop_start_beat
        else:
            self.current_beat = 0.0
        if self.playhead_callback:
            self.playhead_callback(self.current_beat)

    def seek_beat(self, beat: float):
        self.current_beat = max(0.0, beat)
        if self.playhead_callback:
            self.playhead_callback(self.current_beat)

    def preview_note(self, pitch: int, duration_sec: float = 0.35, velocity: int = 100):
        """Joue immédiatement une note (appelé par le Piano Roll lors d'un clic)"""
        wave = SynthVoice.generate_note(pitch, duration_sec, self.sample_rate, velocity)
        with self._preview_lock:
            self._preview_buffers.append({
                "buffer": wave,
                "cursor": 0
            })

    def _audio_callback(self, outdata, frames, time_info, status):
        # Buffer de sortie initialisé à 0
        out = np.zeros((frames, 2), dtype=np.float32)

        # 1. Mixer les sons de prévisualisation (clics clavier piano)
        with self._preview_lock:
            active_previews = []
            for item in self._preview_buffers:
                buf = item["buffer"]
                cur = item["cursor"]
                avail = len(buf) - cur
                to_copy = min(frames, avail)
                if to_copy > 0:
                    out[:to_copy] += buf[cur:cur + to_copy]
                    item["cursor"] += to_copy
                if item["cursor"] < len(buf):
                    active_previews.append(item)
            self._preview_buffers = active_previews

        # 2. Si le transport est en lecture, mixer les pistes du projet
        if self.is_playing and self.project:
            bpm = max(20.0, self.project.bpm)
            beats_per_sec = bpm / 60.0
            beat_step = (frames / self.sample_rate) * beats_per_sec

            start_beat = self.current_beat
            end_beat = start_beat + beat_step

            # Gestion de la boucle
            wrapped = False
            if self.project.loop_enabled and self.project.loop_end_beat > self.project.loop_start_beat:
                if end_beat >= self.project.loop_end_beat:
                    wrapped = True

            # Vérifier les pistes en Solo
            has_solo = any(t.soloed for t in self.project.tracks)

            # Rendu piste par piste
            for track in self.project.tracks:
                if track.muted:
                    continue
                if has_solo and not track.soloed:
                    continue

                track_signal = self._render_track_slice(track, start_beat, end_beat, frames, bpm)
                if track_signal is not None:
                    # Application du volume et panoramique
                    vol = track.volume
                    pan = max(-1.0, min(1.0, track.pan))
                    left_gain = vol * (1.0 - max(0.0, pan))
                    right_gain = vol * (1.0 + min(0.0, pan))
                    track_signal[:, 0] *= left_gain
                    track_signal[:, 1] *= right_gain
                    out += track_signal

            # Avancement de la tête de lecture
            if wrapped and self.project.loop_enabled:
                self.current_beat = self.project.loop_start_beat
            else:
                self.current_beat = end_beat

            # Notification UI
            if self.playhead_callback:
                self.playhead_callback(self.current_beat)

        # Application du volume Master et limitation douce (anti-saturation)
        out *= self.master_volume
        out = np.tanh(out)
        outdata[:] = out

    def _render_track_slice(self, track: Track, start_b: float, end_b: float, frames: int, bpm: float) -> Optional[np.ndarray]:
        track_buf = np.zeros((frames, 2), dtype=np.float32)
        beats_per_sec = bpm / 60.0

        if track.track_type == "midi":
            # Parcourir les clips MIDI
            for clip in track.clips:
                if not isinstance(clip, MidiClip):
                    continue
                clip_start = clip.start_beat
                clip_end = clip_start + clip.length_beats

                # Si le clip intersecte la fenêtre temporelle actuelle
                if clip_end < start_b or clip_start > end_b:
                    continue

                # Vérifier chaque note du clip
                for note in clip.notes:
                    abs_note_start = clip_start + note.start_beat
                    abs_note_end = abs_note_start + note.duration

                    # Si la note intersecte la tranche
                    if abs_note_end > start_b and abs_note_start < end_b:
                        # Calculer où la note tombe dans le buffer [frames]
                        note_dur_sec = note.duration / beats_per_sec
                        wave = SynthVoice.generate_note(note.pitch, note_dur_sec, self.sample_rate, note.velocity)

                        # Offset dans le buffer
                        note_offset_samples = int((abs_note_start - start_b) / beats_per_sec * self.sample_rate)
                        wave_start_sample = 0
                        buf_start_sample = note_offset_samples

                        if buf_start_sample < 0:
                            wave_start_sample = -buf_start_sample
                            buf_start_sample = 0

                        if wave_start_sample < len(wave) and buf_start_sample < frames:
                            to_copy = min(len(wave) - wave_start_sample, frames - buf_start_sample)
                            if to_copy > 0:
                                track_buf[buf_start_sample:buf_start_sample + to_copy] += wave[wave_start_sample:wave_start_sample + to_copy]

        elif track.track_type == "audio":
            # Parcourir les clips Audio
            for clip in track.clips:
                if not isinstance(clip, AudioClip) or clip.audio_data is None:
                    continue
                clip_start = clip.start_beat
                clip_end = clip_start + clip.length_beats
                if clip_end < start_b or clip_start > end_b:
                    continue

                # Position dans les samples du clip
                clip_samples_len = len(clip.audio_data)
                sample_offset = int((start_b - clip_start) / beats_per_sec * self.sample_rate)
                
                buf_start = 0
                clip_read_start = sample_offset
                if clip_read_start < 0:
                    buf_start = -clip_read_start
                    clip_read_start = 0

                if clip_read_start < clip_samples_len and buf_start < frames:
                    to_copy = min(clip_samples_len - clip_read_start, frames - buf_start)
                    if to_copy > 0:
                        samples = clip.audio_data[clip_read_start:clip_read_start + to_copy] * clip.gain
                        if samples.ndim == 1:
                            track_buf[buf_start:buf_start + to_copy, 0] += samples
                            track_buf[buf_start:buf_start + to_copy, 1] += samples
                        else:
                            track_buf[buf_start:buf_start + to_copy] += samples[:, :2]

        return track_buf

    def export_wav(self, file_path: str, end_bar: int = 8) -> bool:
        """Exporte l'arrangement complet dans un fichier WAV haute qualité"""
        if not self.project:
            return False
        bpm = max(20.0, self.project.bpm)
        beats_per_sec = bpm / 60.0
        total_beats = end_bar * self.project.beats_per_bar()
        total_samples = int((total_beats / beats_per_sec) * self.sample_rate)

        rendered = np.zeros((total_samples, 2), dtype=np.float32)
        chunk_size = 4096
        num_chunks = int(np.ceil(total_samples / chunk_size))

        has_solo = any(t.soloed for t in self.project.tracks)

        for i in range(num_chunks):
            start_sample = i * chunk_size
            end_sample = min(total_samples, (i + 1) * chunk_size)
            frames = end_sample - start_sample

            start_b = (start_sample / self.sample_rate) * beats_per_sec
            end_b = (end_sample / self.sample_rate) * beats_per_sec

            out_chunk = np.zeros((frames, 2), dtype=np.float32)
            for track in self.project.tracks:
                if track.muted:
                    continue
                if has_solo and not track.soloed:
                    continue
                sig = self._render_track_slice(track, start_b, end_b, frames, bpm)
                if sig is not None:
                    vol = track.volume
                    pan = max(-1.0, min(1.0, track.pan))
                    sig[:, 0] *= vol * (1.0 - max(0.0, pan))
                    sig[:, 1] *= vol * (1.0 + min(0.0, pan))
                    out_chunk += sig

            rendered[start_sample:end_sample] = out_chunk

        rendered *= self.master_volume
        rendered = np.tanh(rendered)
        sf.write(file_path, rendered, self.sample_rate)
        return True

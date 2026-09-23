"""
core/audio_engine.py - Moteur audio temps réel, synthétiseur polyphonique et mixeur
"""
import threading
import time
from typing import Optional, List, Dict, Callable, Any
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
    def __init__(self, sample_rate: int = 44100, block_size: int = 512, output_device: Optional[int] = None, input_device: Optional[int] = None):
        # Récupération des préférences matérielles si disponibles
        try:
            from core.hardware_manager import hardware_manager
            audio_cfg = hardware_manager.settings.get("audio", {})
            self.sample_rate = int(audio_cfg.get("sample_rate", sample_rate))
            self.block_size = int(audio_cfg.get("buffer_size", block_size))
            self.output_device = audio_cfg.get("output_device_index") if output_device is None else output_device
            self.input_device = audio_cfg.get("input_device_index") if input_device is None else input_device
        except Exception:
            self.sample_rate = sample_rate
            self.block_size = block_size
            self.output_device = output_device
            self.input_device = input_device

        self.project: Optional[Project] = None

        self.is_playing = False
        self.current_beat = 0.0
        self.master_volume = 0.9

        # Enregistrement audio et MIDI
        self.is_recording = False
        self.recording_start_beat = 0.0
        self._input_stream: Optional[sd.InputStream] = None
        self._recorded_audio_blocks: List[np.ndarray] = []
        self._recorded_midi_notes: List[MidiNote] = []
        self._record_lock = threading.Lock()

        # Instances VST3 chargées par piste (track_id -> plugin instance)
        self.track_plugins: Dict[str, Any] = {}

        # File d'attente pour les notes de prévisualisation (clic sur piano roll)
        self._preview_lock = threading.Lock()
        self._preview_buffers: List[Dict] = []

        # Tête de lecture et notification UI
        self.playhead_callback: Optional[Callable[[float], None]] = None
        self._stream: Optional[sd.OutputStream] = None

        self._start_stream()

    def configure_device(self, output_device: Optional[int] = None, input_device: Optional[int] = None, sample_rate: Optional[int] = None, block_size: Optional[int] = None, buffer_size: Optional[int] = None) -> bool:
        """Modifie le périphérique audio, la fréquence d'échantillonnage ou la taille du buffer et redémarre le flux."""
        self.close()
        effective_buffer = buffer_size if buffer_size is not None else block_size
        if output_device is not None:
            self.output_device = None if output_device < 0 else output_device
        if input_device is not None:
            self.input_device = None if input_device < 0 else input_device
        if sample_rate is not None:
            self.sample_rate = int(sample_rate)
        if effective_buffer is not None:
            self.block_size = int(effective_buffer)
        self._start_stream()
        return self._stream is not None

    def play_test_tone(self, freq: float = 440.0, duration_sec: float = 0.6):
        """Joue un accord harmonique doux (Fondamentale, Tierce, Quinte) pour tester la sortie audio."""
        num_samples = max(1, int(self.sample_rate * duration_sec))
        t = np.linspace(0, duration_sec, num_samples, endpoint=False)
        sine1 = np.sin(2.0 * np.pi * freq * t)
        sine2 = 0.6 * np.sin(2.0 * np.pi * (freq * 1.25) * t)  # Tierce majeure
        sine3 = 0.45 * np.sin(2.0 * np.pi * (freq * 1.5) * t)  # Quinte
        chord = (sine1 + sine2 + sine3) * 0.22

        env = np.ones(num_samples, dtype=np.float32)
        att = min(num_samples // 4, int(self.sample_rate * 0.04))
        rel = min(num_samples // 2, int(self.sample_rate * 0.22))
        if att > 0:
            env[:att] = np.linspace(0.0, 1.0, att)
        if rel > 0:
            env[-rel:] = np.linspace(1.0, 0.0, rel)

        chord = (chord * env).astype(np.float32)
        stereo = np.column_stack((chord, chord))

        with self._preview_lock:
            self._preview_buffers.append({
                "buffer": stereo,
                "cursor": 0
            })

    def get_track_plugin(self, track: Track) -> Optional[Any]:
        """Récupère ou initialise l'instance de plugin VST3 associée à une piste"""
        if not track.plugin_path:
            return None
        current = self.track_plugins.get(track.id)
        if current and getattr(current, "_novadaw_path", None) == track.plugin_path:
            return current
        try:
            from core.plugin_manager import global_plugin_manager
            plugin = global_plugin_manager.get_or_load_plugin(track.plugin_path)
            if plugin:
                plugin._novadaw_path = track.plugin_path
                self.track_plugins[track.id] = plugin
                return plugin
        except Exception as e:
            print(f"[AudioEngine] Erreur chargement VST pour piste {track.name}: {e}")
        return None

    def get_effect_plugin(self, file_path: str) -> Optional[Any]:
        """Récupère ou initialise l'instance d'un plugin d'effet pour inserts"""
        if not file_path:
            return None
        current = self.track_plugins.get(file_path)
        if current:
            return current
        try:
            from core.plugin_manager import global_plugin_manager
            plugin = global_plugin_manager.get_or_load_plugin(file_path)
            if plugin:
                self.track_plugins[file_path] = plugin
                return plugin
        except Exception as e:
            print(f"[AudioEngine] Erreur chargement effet {file_path}: {e}")
        return None

    def set_project(self, project: Project):
        self.project = project
        self.current_beat = 0.0
        self.track_plugins.clear()
        with self._preview_lock:
            self._preview_buffers.clear()

    def _start_stream(self):
        try:
            kwargs = {
                "samplerate": self.sample_rate,
                "blocksize": self.block_size,
                "channels": 2,
                "dtype": "float32",
                "callback": self._audio_callback
            }
            if self.output_device is not None and self.output_device >= 0:
                kwargs["device"] = self.output_device

            self._stream = sd.OutputStream(**kwargs)
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
        if self._input_stream:
            try:
                self._input_stream.stop()
                self._input_stream.close()
            except Exception:
                pass
            self._input_stream = None

    def start_recording(self, start_beat: float):
        """Démarre la capture audio en direct et initialise l'enregistrement."""
        self.is_recording = True
        self.recording_start_beat = max(0.0, float(start_beat))
        with self._record_lock:
            self._recorded_audio_blocks.clear()
            self._recorded_midi_notes.clear()

        # Fermer un éventuel flux d'enregistrement précédent
        if self._input_stream:
            try:
                self._input_stream.stop()
                self._input_stream.close()
            except Exception:
                pass
            self._input_stream = None

        # Démarrage du flux de capture d'entrée
        try:
            in_dev = self.input_device
            if in_dev is not None and in_dev < 0:
                in_dev = None

            kwargs = {
                "samplerate": self.sample_rate,
                "blocksize": self.block_size,
                "channels": 2,
                "dtype": "float32",
                "callback": self._record_callback
            }
            if in_dev is not None:
                kwargs["device"] = in_dev

            # Vérifier les canaux disponibles sur le périphérique d'entrée
            try:
                dev_info = sd.query_devices(in_dev, kind="input") if in_dev is not None else sd.query_devices(kind="input")
                max_in = dev_info.get("max_input_channels", 2)
                kwargs["channels"] = min(2, max(1, max_in))
            except Exception:
                pass

            self._input_stream = sd.InputStream(**kwargs)
            self._input_stream.start()
        except Exception as e:
            print(f"[AudioEngine] Avertissement flux de capture audio : {e}")
            self._input_stream = None

    def _record_callback(self, indata, frames, time_info, status):
        """Callback temps réel de capture audio du microphone/ligne"""
        if self.is_recording:
            with self._record_lock:
                self._recorded_audio_blocks.append(indata.copy())

    def stop_recording(self) -> Dict[str, Any]:
        """Arrête l'enregistrement audio et retourne les blocs capturés ainsi que les notes MIDI."""
        self.is_recording = False
        if self._input_stream:
            try:
                self._input_stream.stop()
                self._input_stream.close()
            except Exception:
                pass
            self._input_stream = None

        with self._record_lock:
            blocks = list(self._recorded_audio_blocks)
            self._recorded_audio_blocks.clear()
            midi_notes = list(self._recorded_midi_notes)
            self._recorded_midi_notes.clear()

        audio_data = None
        if blocks:
            try:
                raw_audio = np.concatenate(blocks, axis=0)
                if raw_audio.ndim == 1:
                    audio_data = np.column_stack((raw_audio, raw_audio))
                elif raw_audio.shape[1] == 1:
                    audio_data = np.column_stack((raw_audio[:, 0], raw_audio[:, 0]))
                else:
                    audio_data = raw_audio[:, :2]
            except Exception as e:
                print(f"[AudioEngine] Erreur assemblage des blocs audio enregistrés : {e}")

        return {
            "start_beat": self.recording_start_beat,
            "audio": audio_data,
            "midi_notes": midi_notes
        }

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
        self._reset_all_plugins()
        if self.playhead_callback:
            self.playhead_callback(self.current_beat)

    def seek_beat(self, beat: float):
        self.current_beat = max(0.0, beat)
        self._reset_all_plugins()
        if self.playhead_callback:
            self.playhead_callback(self.current_beat)

    def _reset_all_plugins(self):
        """Réinitialise les mémoires des filtres et compresseurs lors d'un arrêt ou repositionnement"""
        if not self.project:
            return
        for track in self.project.tracks:
            for p in getattr(track, "plugins", []):
                if hasattr(p, "reset"):
                    p.reset()
        if getattr(self.project, "master_track", None):
            for p in getattr(self.project.master_track, "plugins", []):
                if hasattr(p, "reset"):
                    p.reset()

    def _get_active_mixer_plugin(self) -> Optional[Any]:
        """Trouve le plugin mixeur actif (généralement sur la piste Master)"""
        if not self.project:
            return None
        if getattr(self.project, "master_track", None):
            for p in self.project.master_track.plugins:
                if getattr(p, "plugin_type_id", None) == "novadaw.mixer":
                    return p
        for t in self.project.tracks:
            for p in getattr(t, "plugins", []):
                if getattr(p, "plugin_type_id", None) == "novadaw.mixer":
                    return p
        return None

    def _can_render_vst_offline(self, plugin: Any) -> bool:
        """
        Vérifie si un instrument VST3 supporte le rendu MIDI hors ligne via Pedalboard/JUCE.
        Certains samplers multi-bus complexes (ex: Kontakt, SampleTank) requièrent un hôte de
        routage avancé et peuvent générer un accès mémoire natif hors d'une interface audio dédiée.
        Pour ces plugins, NovaDAW bascule automatiquement sur son synthétiseur polyphonique interne
        afin de garantir une stabilité absolue et un retour sonore instantané pour composer.
        """
        if not plugin or not getattr(plugin, "is_instrument", False):
            return False
        cached = getattr(plugin, "_supports_offline_midi", None)
        if cached is not None:
            return cached

        name = getattr(plugin, "name", "")
        known_incompatible = ["Kontakt", "SampleTank"]
        if any(k.lower() in name.lower() for k in known_incompatible):
            plugin._supports_offline_midi = False
            return False

        plugin._supports_offline_midi = True
        return True

    def get_native_instrument(self, track: Optional[Track]) -> Optional[Any]:
        """Récupère l'instance d'instrument virtuel natif (ex: Nova Drums VSTi) associée à la piste"""
        if not track:
            return None
        # 1. Vérifier si un instrument est déjà présent dans la pile plugins de la piste
        for p in getattr(track, "plugins", []):
            if getattr(p, "is_instrument", False) or getattr(p, "category", "") == "instrument":
                return p
        # 2. Si track.plugin_path pointe vers un plugin natif (ex: novadaw.drum_machine)
        if track.plugin_path and str(track.plugin_path).startswith("novadaw."):
            try:
                from plugins.registry import plugin_registry, ensure_plugins_loaded
                ensure_plugins_loaded()
                plugin = plugin_registry.create_plugin(track.plugin_path)
                if plugin:
                    track.plugins.insert(0, plugin)
                    return plugin
            except Exception as e:
                print(f"[AudioEngine] Erreur initialisation instrument natif {track.plugin_path}: {e}")
        return None

    def preview_note(self, pitch: int, duration_sec: float = 0.35, velocity: int = 100, track: Optional[Track] = None):
        """Joue immédiatement une note (appelé par le Piano Roll lors d'un clic)"""
        wave = None
        # 1. Vérifier si la piste est routée vers un instrument natif (Nova Drums VSTi)
        native_inst = self.get_native_instrument(track) if track else None
        if native_inst and hasattr(native_inst, "render_note"):
            try:
                wave = native_inst.render_note(pitch, duration_sec=duration_sec, sample_rate=self.sample_rate, velocity=velocity)
            except Exception as e:
                wave = None

        if wave is None and track and track.plugin_path:
            vst_plugin = self.get_track_plugin(track)
            if self._can_render_vst_offline(vst_plugin):
                try:
                    on_msg = (bytes([0x90, pitch, max(1, min(127, velocity))]), 0.0)
                    off_msg = (bytes([0x80, pitch, 0]), duration_sec * 0.8)
                    vst_out = vst_plugin([on_msg, off_msg], duration=duration_sec, sample_rate=self.sample_rate, num_channels=2, reset=False)
                    if vst_out is not None and vst_out.size > 0:
                        if vst_out.ndim == 1:
                            wave = np.column_stack((vst_out, vst_out))
                        elif vst_out.shape[0] == 1:
                            wave = np.column_stack((vst_out[0], vst_out[0]))
                        else:
                            wave = vst_out[:2].T
                except Exception as e:
                    wave = None

        if wave is None:
            wave = SynthVoice.generate_note(pitch, duration_sec, self.sample_rate, velocity)

        with self._preview_lock:
            self._preview_buffers.append({
                "buffer": wave,
                "cursor": 0
            })

        # Enregistrement des notes MIDI si l'enregistrement est actif sur une piste MIDI armée
        if self.is_recording and track and track.track_type == "midi" and getattr(track, "armed", False):
            bpm = self.project.bpm if self.project else 120.0
            dur_beats = max(0.25, (duration_sec * bpm) / 60.0)
            rel_beat = max(0.0, self.current_beat - self.recording_start_beat)
            with self._record_lock:
                self._recorded_midi_notes.append(MidiNote(
                    pitch=pitch,
                    start_beat=rel_beat,
                    duration=dur_beats,
                    velocity=velocity
                ))

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

        # 3. Traitement de la piste Master et de sa pile de plugins (Mixeur, EQ, Compresseur)
        if self.project and getattr(self.project, "master_track", None):
            master_t = self.project.master_track
            if not master_t.muted:
                vol = master_t.volume
                pan = max(-1.0, min(1.0, master_t.pan))
                out[:, 0] *= vol * (1.0 - max(0.0, pan))
                out[:, 1] *= vol * (1.0 + min(0.0, pan))
            if hasattr(master_t, "plugins") and master_t.plugins:
                for plugin in master_t.plugins:
                    if getattr(plugin, "enabled", True):
                        try:
                            out = plugin.process(out, self.sample_rate)
                        except Exception as e:
                            pass

        # Application du volume Master et limitation douce (anti-saturation)
        out *= self.master_volume
        out = np.tanh(out)
        outdata[:] = out

    def _render_track_slice(self, track: Track, start_b: float, end_b: float, frames: int, bpm: float) -> Optional[np.ndarray]:
        track_buf = np.zeros((frames, 2), dtype=np.float32)
        beats_per_sec = bpm / 60.0

        if track.track_type == "midi":
            # 1. Vérifier si la piste est routée vers un instrument virtuel natif (ex: Nova Drums VSTi)
            native_inst = self.get_native_instrument(track)
            rendered_by_native = False

            if native_inst and hasattr(native_inst, "render_slice"):
                clip_notes = []
                for clip in track.clips:
                    if not isinstance(clip, MidiClip):
                        continue
                    clip_start = clip.start_beat
                    clip_end = clip_start + clip.length_beats
                    if clip_end < start_b or clip_start > end_b:
                        continue
                    for note in clip.notes:
                        abs_note_start = clip_start + note.start_beat
                        if abs_note_start < end_b and (abs_note_start + note.duration) > start_b:
                            clip_notes.append(MidiNote(
                                pitch=note.pitch,
                                start_beat=abs_note_start,
                                duration=note.duration,
                                velocity=note.velocity
                            ))
                try:
                    native_out = native_inst.render_slice(
                        clip_notes, start_b, end_b, beats_per_sec, frames, self.sample_rate
                    )
                    if native_out is not None and len(native_out) > 0:
                        n = min(frames, len(native_out))
                        track_buf[:n] += native_out[:n]
                        rendered_by_native = True
                except Exception as e:
                    rendered_by_native = False

            # 2. Vérifier si la piste est routée vers un VST3 Instrument externe
            vst_plugin = None if rendered_by_native else self.get_track_plugin(track)
            rendered_by_vst = False

            if not rendered_by_native and self._can_render_vst_offline(vst_plugin):
                try:
                    dur_sec = frames / self.sample_rate
                    midi_messages = []
                    has_notes = False

                    for clip in track.clips:
                        if not isinstance(clip, MidiClip):
                            continue
                        clip_start = clip.start_beat
                        clip_end = clip_start + clip.length_beats
                        if clip_end < start_b or clip_start > end_b:
                            continue

                        for note in clip.notes:
                            abs_note_start = clip_start + note.start_beat
                            abs_note_end = abs_note_start + note.duration

                            # Détection Note-On dans cette tranche
                            if start_b <= abs_note_start < end_b:
                                offset = max(0.0, min(dur_sec, (abs_note_start - start_b) / beats_per_sec))
                                midi_messages.append((bytes([0x90, note.pitch, max(1, min(127, note.velocity))]), offset))
                                has_notes = True

                            # Détection Note-Off dans cette tranche
                            if start_b <= abs_note_end < end_b:
                                offset = max(0.0, min(dur_sec, (abs_note_end - start_b) / beats_per_sec))
                                midi_messages.append((bytes([0x80, note.pitch, 0]), offset))
                            elif abs_note_start < end_b and abs_note_end > start_b:
                                has_notes = True

                    if midi_messages or has_notes:
                        vst_out = vst_plugin(midi_messages, duration=dur_sec, sample_rate=self.sample_rate, num_channels=2, reset=False)
                        if vst_out is not None and vst_out.size > 0:
                            if vst_out.ndim == 1:
                                n = min(frames, len(vst_out))
                                track_buf[:n, 0] += vst_out[:n]
                                track_buf[:n, 1] += vst_out[:n]
                            elif vst_out.shape[0] == 1:
                                n = min(frames, vst_out.shape[1])
                                track_buf[:n, 0] += vst_out[0, :n]
                                track_buf[:n, 1] += vst_out[0, :n]
                            else:
                                n = min(frames, vst_out.shape[1])
                                track_buf[:n, 0] += vst_out[0, :n]
                                track_buf[:n, 1] += vst_out[1, :n]
                        rendered_by_vst = True
                except Exception as e:
                    # En cas d'erreur avec le VST, le synthétiseur interne prendra le relais
                    rendered_by_vst = False

            # 2. Si pas de VST ou échec de rendu, fallback sur le synthétiseur polyphonique interne
            if not rendered_by_vst:
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

        # 3. Traitement des effets d'insert (Insert FX Chain style Cubase)
        if hasattr(track, "insert_effects") and track.insert_effects and track_buf is not None:
            for fx_path in track.insert_effects:
                if not fx_path:
                    continue
                fx_plugin = self.get_effect_plugin(fx_path)
                if fx_plugin and getattr(fx_plugin, "is_effect", False):
                    try:
                        # Pedalboard attend un buffer (channels, frames)
                        in_audio = track_buf.T
                        fx_out = fx_plugin(in_audio, sample_rate=self.sample_rate, reset=False)
                        if fx_out is not None and fx_out.size > 0:
                            if fx_out.ndim == 1:
                                n = min(frames, len(fx_out))
                                track_buf[:n, 0] = fx_out[:n]
                                track_buf[:n, 1] = fx_out[:n]
                            elif fx_out.shape[0] == 1:
                                n = min(frames, fx_out.shape[1])
                                track_buf[:n, 0] = fx_out[0, :n]
                                track_buf[:n, 1] = fx_out[0, :n]
                            else:
                                n = min(frames, fx_out.shape[1])
                                track_buf[:n, 0] = fx_out[0, :n]
                                track_buf[:n, 1] = fx_out[1, :n]
                    except Exception as e:
                        pass

        # 4. Traitement de la pile de plugins modulaires de la piste (Égaliseur, Compresseur, etc.)
        if hasattr(track, "plugins") and track.plugins and track_buf is not None:
            for plugin in track.plugins:
                if track.track_type == "midi" and plugin is native_inst:
                    continue
                if getattr(plugin, "enabled", True):
                    try:
                        track_buf = plugin.process(track_buf, self.sample_rate)
                    except Exception as e:
                        pass

        # 5. Envoi des niveaux de crête au plugin Mixeur pour les VU-mètres
        mixer_plugin = self._get_active_mixer_plugin()
        if mixer_plugin and track_buf is not None and len(track_buf) > 0:
            pk_l = float(np.max(np.abs(track_buf[:, 0])))
            pk_r = float(np.max(np.abs(track_buf[:, 1])))
            mixer_plugin.update_track_peak(track.id, pk_l, pk_r)

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

            # Traitement Master sur le mixage global
            if self.project and getattr(self.project, "master_track", None):
                master_t = self.project.master_track
                if not master_t.muted:
                    vol = master_t.volume
                    pan = max(-1.0, min(1.0, master_t.pan))
                    out_chunk[:, 0] *= vol * (1.0 - max(0.0, pan))
                    out_chunk[:, 1] *= vol * (1.0 + min(0.0, pan))
                if hasattr(master_t, "plugins") and master_t.plugins:
                    for plugin in master_t.plugins:
                        if getattr(plugin, "enabled", True):
                            try:
                                out_chunk = plugin.process(out_chunk, self.sample_rate)
                            except Exception:
                                pass

            rendered[start_sample:end_sample] = out_chunk

        rendered *= self.master_volume
        rendered = np.tanh(rendered)
        sf.write(file_path, rendered, self.sample_rate)
        return True

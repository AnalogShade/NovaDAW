"""
core/audio_engine.py - Moteur audio temps réel, synthétiseur polyphonique et mixeur
"""
import os
import threading
import time
from typing import Optional, List, Dict, Callable, Any
import numpy as np
try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except (ImportError, OSError):
    sd = None
    HAS_SOUNDDEVICE = False
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


_global_audio_engine: Optional['AudioEngine'] = None


def get_global_audio_engine() -> Optional['AudioEngine']:
    """Retourne l'instance globale active d'AudioEngine si disponible."""
    global _global_audio_engine
    return _global_audio_engine


class AudioEngine:
    @classmethod
    def get_instance(cls) -> Optional['AudioEngine']:
        global _global_audio_engine
        return _global_audio_engine

    def __init__(self, sample_rate: Optional[int] = None, block_size: Optional[int] = None, output_device: Optional[int] = None, input_device: Optional[int] = None):
        global _global_audio_engine
        _global_audio_engine = self

        # Configuration automatique universelle du matériel audio
        try:
            from core.hardware_manager import hardware_manager
            audio_cfg = hardware_manager.settings.get("audio", {})
            out_idx = audio_cfg.get("output_device_index") if output_device is None else output_device

            # Valider si le périphérique sauvegardé existe encore ou lancer l'auto-configuration
            if out_idx is None or (HAS_SOUNDDEVICE and sd and out_idx >= len(sd.query_devices())):
                audio_cfg = hardware_manager.auto_configure_audio()
                out_idx = audio_cfg.get("output_device_index")

            self.output_device = out_idx
            self.input_device = audio_cfg.get("input_device_index") if input_device is None else input_device
            if self.output_device is None:
                self.output_device = hardware_manager.get_default_output_device_index()
            if self.input_device is None:
                self.input_device = hardware_manager.get_default_input_device_index()

            optimal_sr = hardware_manager.get_optimal_sample_rate(self.output_device)
            self.sample_rate = int(audio_cfg.get("sample_rate", optimal_sr)) if sample_rate is None else sample_rate
            optimal_buf = hardware_manager.get_optimal_buffer_size(self.output_device, self.sample_rate)
            self.block_size = int(audio_cfg.get("buffer_size", optimal_buf)) if block_size is None else block_size
        except Exception:
            self.sample_rate = sample_rate if sample_rate is not None else 44100
            self.block_size = block_size if block_size is not None else 512
            self.output_device = output_device
            self.input_device = input_device

        self.project: Optional[Project] = None

        self.is_playing = False
        self.current_beat = 0.0
        self.master_volume = 0.9

        # Système de Pré-rendu Audio et Cache ASIO-Guard en RAM (Hybrid Audio Engine)
        self._track_audio_caches: Dict[str, Dict[str, Any]] = {}
        self._track_cache_lock = threading.Lock()
        self._asio_guard_enabled: bool = True

        # Enregistrement audio et MIDI
        self.is_recording = False
        self.recording_start_beat = 0.0
        self._input_stream: Optional[sd.InputStream] = None
        self._recorded_audio_blocks: List[np.ndarray] = []
        self._recorded_midi_notes: List[MidiNote] = []
        self._record_lock = threading.Lock()
        self.record_latency_compensation_samples: int = 0

        # Instances VST3 chargées par piste (track_id -> plugin instance)
        self.plugin_errors = {}
        self.track_plugins: Dict[str, Any] = {}

        # File d'attente pour les notes de prévisualisation (clic sur piano roll)
        self._preview_lock = threading.Lock()
        self._preview_buffers: List[Dict] = []

        # Mesure des niveaux de crête et distorsion Master (VU-mètre)
        self._master_peak_l: float = 0.0
        self._master_peak_r: float = 0.0
        self._master_clipped: bool = False
        self._master_clip_time: float = 0.0
        self._master_peak_lock = threading.Lock()

        # Tête de lecture et notification UI
        self.playhead_callback: Optional[Callable[[float], None]] = None
        self.caching_progress_callback: Optional[Callable[[int, int, str], None]] = None
        self._stream: Optional[sd.OutputStream] = None

        self._start_stream()

    def configure_device(self, output_device: Optional[int] = None, input_device: Optional[int] = None, sample_rate: Optional[int] = None, block_size: Optional[int] = None, buffer_size: Optional[int] = None, record_offset_samples: Optional[int] = None, asio_guard: Optional[bool] = None) -> bool:
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
        if record_offset_samples is not None:
            self.record_latency_compensation_samples = int(record_offset_samples)
        if asio_guard is not None:
            self._asio_guard_enabled = bool(asio_guard)
        self._start_stream()
        return self._stream is not None or os.environ.get("NOVADAW_SILENT_TESTS") == "1"

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
        if not track.plugin_path or track.plugin_path.startswith("novadaw."):
            return None
        try:
            from core.plugin_manager import global_plugin_manager
            plugin = global_plugin_manager.get_ready_plugin(track.plugin_path, f"track:{track.id}:instrument:{track.plugin_path}")
            if plugin:
                plugin._novadaw_path = track.plugin_path
                self.track_plugins[track.id] = plugin
                return plugin
        except Exception as e:
            print(f"[AudioEngine] Erreur chargement VST pour piste {track.name}: {e}")
        return None

    def get_effect_plugin(self, file_path: str, instance_key=None) -> Optional[Any]:
        """Récupère ou initialise l'instance d'un plugin d'effet pour inserts"""
        if not file_path:
            return None
        key = instance_key or file_path
        try:
            from core.plugin_manager import global_plugin_manager
            plugin = global_plugin_manager.get_ready_plugin(file_path, key)
            if plugin:
                self.track_plugins[key] = plugin
                return plugin
        except Exception as e:
            print(f"[AudioEngine] Erreur chargement effet {file_path}: {e}")
        return None

    def set_project(self, project: Project):
        self.is_playing = False
        from core.plugin_manager import global_plugin_manager
        global_plugin_manager.restore_states(project.plugin_states)
        global_plugin_manager.current_project = project
        self.project = project
        self.current_beat = 0.0
        self.track_plugins.clear()
        with self._preview_lock:
            self._preview_buffers.clear()
        self.invalidate_all_track_caches()
        self._ensure_tracks_cached_async()

    def _start_stream(self):
        if os.environ.get("NOVADAW_SILENT_TESTS") == "1":
            self._stream = None
            return

        from core.hardware_manager import hardware_manager

        target_device = self.output_device
        if target_device is not None and target_device < 0:
            target_device = None

        # Valider dynamiquement la fréquence optimale acceptée par le périphérique cible
        valid_sr = hardware_manager.get_optimal_sample_rate(target_device)
        candidate_srs = [valid_sr, self.sample_rate, 44100, 48000]
        seen = set()
        candidate_srs = [x for x in candidate_srs if not (x in seen or seen.add(x))]

        opened = False
        for sr in candidate_srs:
            try:
                # Tester d'abord la validité matérielle sans émettre d'erreur
                if target_device is not None:
                    sd.check_output_settings(device=target_device, samplerate=sr)
                else:
                    sd.check_output_settings(samplerate=sr)

                kwargs = {
                    "samplerate": sr,
                    "blocksize": self.block_size,
                    "channels": 2,
                    "dtype": "float32",
                    "callback": self._audio_callback
                }
                if target_device is not None:
                    kwargs["device"] = target_device

                self._stream = sd.OutputStream(**kwargs)
                self._stream.start()
                self.sample_rate = sr
                opened = True
                print(f"[AudioEngine] Flux audio démarré avec succès (device={target_device}, sr={sr}, block={self.block_size})")
                break
            except Exception:
                continue

        # Si le périphérique a échoué (ex: débranché ou invalide), déclencher l'auto-détection universelle
        if not opened:
            print("[AudioEngine] Repli vers auto-détection universelle du matériel...")
            try:
                new_cfg = hardware_manager.auto_configure_audio(force=True)
                new_dev = new_cfg.get("output_device_index")
                new_sr = new_cfg.get("sample_rate", 44100)
                new_buf = new_cfg.get("buffer_size", 512)

                kwargs = {
                    "samplerate": new_sr,
                    "blocksize": new_buf,
                    "channels": 2,
                    "dtype": "float32",
                    "callback": self._audio_callback
                }
                if new_dev is not None and new_dev >= 0:
                    kwargs["device"] = new_dev

                self._stream = sd.OutputStream(**kwargs)
                self._stream.start()
                self.output_device = new_dev
                self.sample_rate = new_sr
                self.block_size = new_buf
                opened = True
                print(f"[AudioEngine] Flux audio rétabli avec succès via auto-détection ({new_sr} Hz) !")
            except Exception as e_fallback:
                print(f"[AudioEngine] Erreur critique d'initialisation du périphérique de secours: {e_fallback}")
                self._stream = None

        if not opened:
            print("[AudioEngine] Avertissement : Impossible d'ouvrir un flux audio physique.")
            self._stream = None

    def close(self):
        global _global_audio_engine
        if _global_audio_engine is self:
            _global_audio_engine = None
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

        # Mode silencieux pour tests unitaires
        if os.environ.get("NOVADAW_SILENT_TESTS") == "1":
            try:
                self._input_stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.block_size,
                    channels=2,
                    callback=self._record_callback
                )
                self._input_stream.start()
            except Exception:
                self._input_stream = None
            return

        # Démarrage du flux de capture d'entrée physique
        try:
            in_dev = self.input_device
            if in_dev is not None and in_dev < 0:
                in_dev = None

            # Identifier les caractéristiques du microphone
            dev_default_sr = self.sample_rate
            in_channels = 2
            try:
                dev_info = sd.query_devices(in_dev, kind="input") if in_dev is not None else sd.query_devices(kind="input")
                max_in = dev_info.get("max_input_channels", 2)
                in_channels = min(2, max(1, max_in))
                dev_default_sr = int(dev_info.get("default_samplerate", self.sample_rate))
            except Exception:
                pass

            kwargs = {
                "samplerate": self.sample_rate,
                "blocksize": self.block_size,
                "channels": in_channels,
                "dtype": "float32",
                "callback": self._record_callback
            }
            if in_dev is not None:
                kwargs["device"] = in_dev

            self._record_sample_rate = self.sample_rate
            try:
                self._input_stream = sd.InputStream(**kwargs)
                self._input_stream.start()
            except Exception as e_first:
                # Si la fréquence projet (ex: 48kHz ou 44.1kHz) est refusée par le micro,
                # tentative automatique avec la fréquence native du périphérique
                if dev_default_sr != self.sample_rate:
                    try:
                        kwargs["samplerate"] = dev_default_sr
                        self._input_stream = sd.InputStream(**kwargs)
                        self._input_stream.start()
                        self._record_sample_rate = dev_default_sr
                    except Exception as e_second:
                        print(f"[AudioEngine] Échec capture micro (44.1k/48k fallback): {e_second}")
                        self._input_stream = None
                else:
                    print(f"[AudioEngine] Avertissement flux de capture audio : {e_first}")
                    self._input_stream = None

        except Exception as e:
            print(f"[AudioEngine] Erreur critique initialisation micro : {e}")
            self._input_stream = None

    def _record_callback(self, indata, frames, time_info, status):
        """Callback temps réel de capture audio du microphone/ligne"""
        if self.is_recording:
            with self._record_lock:
                self._recorded_audio_blocks.append(indata.copy())

    def get_live_recording_audio(self) -> Optional[np.ndarray]:
        """Retourne une copie concaténée des blocs audio capturés en direct (thread-safe)."""
        if not self.is_recording:
            return None
        with self._record_lock:
            if not self._recorded_audio_blocks:
                return None
            blocks = list(self._recorded_audio_blocks)

        try:
            raw = np.concatenate(blocks, axis=0)
            if raw.ndim == 1:
                return np.column_stack((raw, raw))
            elif raw.shape[1] == 1:
                return np.column_stack((raw[:, 0], raw[:, 0]))
            return raw[:, :2]
        except Exception:
            return None

    def get_live_recording_notes(self) -> List[MidiNote]:
        """Retourne une copie des notes MIDI capturées en direct (thread-safe)."""
        if not self.is_recording:
            return []
        with self._record_lock:
            return list(self._recorded_midi_notes)

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

                rec_sr = getattr(self, "_record_sample_rate", self.sample_rate)
                if rec_sr != self.sample_rate and len(audio_data) > 0:
                    try:
                        from core.audio_importer import resample_poly, HAS_SCIPY
                        from math import gcd
                        if HAS_SCIPY:
                            div = gcd(self.sample_rate, rec_sr)
                            up = self.sample_rate // div
                            down = rec_sr // div
                            l = resample_poly(audio_data[:, 0], up, down).astype(np.float32)
                            r = resample_poly(audio_data[:, 1], up, down).astype(np.float32)
                            audio_data = np.column_stack((l, r))
                        else:
                            new_len = int(len(audio_data) * (self.sample_rate / rec_sr))
                            idx_orig = np.linspace(0, len(audio_data) - 1, new_len)
                            l = np.interp(idx_orig, np.arange(len(audio_data)), audio_data[:, 0]).astype(np.float32)
                            r = np.interp(idx_orig, np.arange(len(audio_data)), audio_data[:, 1]).astype(np.float32)
                            audio_data = np.column_stack((l, r))
                    except Exception as e_resamp:
                        print(f"[AudioEngine] Avertissement rééchantillonnage audio enregistré : {e_resamp}")
            except Exception as e:
                print(f"[AudioEngine] Erreur assemblage des blocs audio enregistrés : {e}")

        # Compensation de latence d'enregistrement matérielle (Record Offset style Cubase 6)
        offset = getattr(self, "record_latency_compensation_samples", 0)
        if offset != 0 and audio_data is not None and len(audio_data) > abs(offset):
            if offset > 0:
                audio_data = audio_data[offset:]
            else:
                pad = np.zeros((-offset, audio_data.shape[1]), dtype=np.float32)
                audio_data = np.vstack((pad, audio_data))

        return {
            "start_beat": self.recording_start_beat,
            "audio": audio_data,
            "midi_notes": midi_notes
        }


    def play(self):
        """Démarre la lecture IMMÉDIATEMENT (0 ms de latence UI)."""
        self.is_playing = True

        # S'assurer que le flux audio physique est actif
        if (self._stream is None or not getattr(self._stream, "active", False)) and os.environ.get("NOVADAW_SILENT_TESTS") != "1":
            self._start_stream()

        # Pré-chauffage et pré-calcul des caches ASIO-Guard en arrière-plan (zéro blocage UI)
        if self.project:
            for track in self.project.tracks:
                inst = self.get_native_instrument(track)
                if inst and hasattr(inst, "warm_up_cache"):
                    try:
                        inst.warm_up_cache(async_bg=True)
                    except Exception:
                        pass
            if self._asio_guard_enabled:
                self._ensure_tracks_cached_async()

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
        for plugin in list(self.track_plugins.values()):
            try:
                plugin.reset()
            except Exception:
                pass
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
        """Native faults are contained by the isolated VST host."""
        if not plugin or not getattr(plugin, "is_instrument", False):
            return False
        return True

    def get_native_instrument(self, track: Optional[Track]) -> Optional[Any]:
        """Récupère l'instance d'instrument virtuel natif (ex: Nova Drums VSTi) associée à la piste"""
        if not track:
            return None
        # 1. Vérifier si un instrument est déjà présent dans la pile plugins de la piste
        for p in getattr(track, "plugins", []):
            if (getattr(p, "is_instrument", False) or getattr(p, "category", "") == "instrument") and (not track.plugin_path or getattr(p, "plugin_type_id", None) == track.plugin_path):
                return p
        # 2. Si track.plugin_path pointe vers un plugin natif (ex: novadaw.drum_machine)
        if track.plugin_path and str(track.plugin_path).startswith("novadaw."):
            try:
                from plugins.registry import plugin_registry, ensure_plugins_loaded
                ensure_plugins_loaded()
                plugin = plugin_registry.create_plugin(track.plugin_path)
                if plugin:
                    if self.project:
                        template = self.project.get_rack_native_plugin(track.plugin_path)
                        if template:
                            plugin.set_state(template.get_state())
                    track.plugins.insert(0, plugin)
                    return plugin
            except Exception as e:
                print(f"[AudioEngine] Erreur initialisation instrument natif {track.plugin_path}: {e}")
        return None

    def preview_note(self, pitch: int, duration_sec: float = 0.35, velocity: int = 100, track: Optional[Track] = None):
        """Joue immédiatement une note (appelé par le Piano Roll lors d'un clic)"""
        wave = None
        # 1. Vérifier si la piste est routée vers un instrument natif (Nova Drums VSTi, NovaSynth)
        native_inst = self.get_native_instrument(track) if track else None
        if native_inst and hasattr(native_inst, "render_note"):
            try:
                bus_idx = getattr(track, "synth_output_bus", None) if track else None
                wave = native_inst.render_note(pitch, duration_sec=duration_sec, sample_rate=self.sample_rate, velocity=velocity, bus_index=bus_idx)
            except TypeError:
                try:
                    wave = native_inst.render_note(pitch, duration_sec=duration_sec, sample_rate=self.sample_rate, velocity=velocity)
                except Exception:
                    wave = None
            except Exception:
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

        if wave is None and track and track.plugin_path:
            return
        if wave is None:
            wave = SynthVoice.generate_note(pitch, duration_sec, self.sample_rate, velocity)

        # Application de la chaîne d'effets de la piste (Égaliseur, Compresseur, VST) et du volume/pan
        if wave is not None and len(wave) > 0 and track:
            wave = self._process_track_effects_isolated(track, wave.copy(), native_inst)
            vol = float(getattr(track, "volume", 0.8))
            pan = max(-1.0, min(1.0, float(getattr(track, "pan", 0.0))))
            left_gain = vol * (1.0 - max(0.0, pan))
            right_gain = vol * (1.0 + min(0.0, pan))
            wave[:, 0] *= left_gain
            wave[:, 1] *= right_gain

        with self._preview_lock:
            self._preview_buffers.append({
                "buffer": np.ascontiguousarray(wave, dtype=np.float32),
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

    def _process_track_effects_isolated(self, track: Track, audio: np.ndarray, native_inst: Optional[Any] = None) -> np.ndarray:
        """
        Traite le buffer audio d'une note ou prévisualisation à travers tous les effets de la piste
        (plugins natifs comme l'Égaliseur et Compresseur, ainsi qu'effets VST3)
        sans perturber les mémoires de délai et filtres de la lecture continue du projet.
        Exécution ultra-rapide (< 0.2 ms), aucune latence perceptible.
        """
        if track is None or audio is None or len(audio) == 0:
            return audio

        # S'assurer d'avoir un buffer float32 stéréo modifiable
        if audio.ndim == 1:
            out_audio = np.column_stack((audio, audio)).astype(np.float32)
        else:
            out_audio = audio.copy().astype(np.float32)

        # 1. Effets VST3 d'inserts
        if hasattr(track, "insert_effects") and track.insert_effects:
            for fx_path in track.insert_effects:
                if not fx_path:
                    continue
                fx_plugin = self.get_effect_plugin(fx_path, f"track:{track.id}:effect:{fx_path}")
                if fx_plugin and getattr(fx_plugin, "is_effect", False):
                    try:
                        frames = len(out_audio)
                        in_audio = out_audio.T
                        fx_out = fx_plugin(in_audio, sample_rate=self.sample_rate, reset=False)
                        if fx_out is not None and fx_out.size > 0:
                            if fx_out.ndim == 1:
                                n = min(frames, len(fx_out))
                                out_audio[:n, 0] = fx_out[:n]
                                out_audio[:n, 1] = fx_out[:n]
                            elif fx_out.shape[0] == 1:
                                n = min(frames, fx_out.shape[1])
                                out_audio[:n, 0] = fx_out[0, :n]
                                out_audio[:n, 1] = fx_out[0, :n]
                            else:
                                n = min(frames, fx_out.shape[1])
                                out_audio[:n, 0] = fx_out[0, :n]
                                out_audio[:n, 1] = fx_out[1, :n]
                    except Exception:
                        pass

        # 2. Plugins natifs empilés (Égaliseur, Compresseur, etc.)
        if hasattr(track, "plugins") and track.plugins:
            for plugin in track.plugins:
                # Sauter l'instrument générateur
                if plugin is native_inst or getattr(plugin, "is_instrument", False):
                    continue
                if not getattr(plugin, "enabled", True):
                    continue

                # Pour l'égaliseur : préserver l'état continu si la lecture tourne pour éliminer tout risque de saut audio
                saved_states = {}
                is_eq = getattr(plugin, "plugin_type_id", None) == "novadaw.equalizer"
                if is_eq and self.is_playing:
                    for b in getattr(plugin, "bands", []):
                        saved_states[b] = (
                            b.zi_left.copy() if b.zi_left is not None else None,
                            b.zi_right.copy() if b.zi_right is not None else None
                        )

                try:
                    out_audio = plugin.process(out_audio, self.sample_rate)
                except Exception:
                    pass

                # Restaurer les états continus si la lecture est active
                if is_eq and self.is_playing:
                    for b, (zl, zr) in saved_states.items():
                        b.zi_left = zl
                        b.zi_right = zr

        return out_audio

    def get_track_for_plugin(self, plugin: Any) -> Optional[Track]:
        """Retrouve la piste qui héberge le plugin donné."""
        if not self.project:
            return None
        for t in self.project.tracks:
            if hasattr(t, "plugins") and plugin in t.plugins:
                return t
            if t.plugin_path == getattr(plugin, "plugin_type_id", None):
                return t
        return None

    def play_preview_buffer(self, wave: np.ndarray, choke_group: int = 0, track: Optional[Track] = None) -> bool:
        """
        Joue immédiatement un buffer audio stéréo en direct dans le flux en temps réel (latence < 10ms).
        Prend en charge les choke groups et applique la chaîne d'inserts de la piste si spécifiée.
        """
        if wave is None or len(wave) == 0:
            return False
        if not (self._stream and getattr(self._stream, "active", False)):
            return False

        if track is not None:
            native_inst = self.get_native_instrument(track)
            wave = self._process_track_effects_isolated(track, wave.copy(), native_inst)
            vol = float(getattr(track, "volume", 0.8))
            pan = max(-1.0, min(1.0, float(getattr(track, "pan", 0.0))))
            left_gain = vol * (1.0 - max(0.0, pan))
            right_gain = vol * (1.0 + min(0.0, pan))
            wave[:, 0] *= left_gain
            wave[:, 1] *= right_gain

        with self._preview_lock:
            if choke_group > 0:
                # Choke group : étouffer immédiatement les voix actives du même groupe
                for item in self._preview_buffers:
                    if item.get("choke_group") == choke_group:
                        buf = item["buffer"]
                        cur = item["cursor"]
                        rem = len(buf) - cur
                        if rem > 0:
                            fade_len = min(rem, int(0.005 * self.sample_rate))
                            buf[cur:cur + fade_len] *= np.linspace(1.0, 0.0, fade_len)[:, None]
                            item["buffer"] = buf[:cur + fade_len]

            self._preview_buffers.append({
                "buffer": np.ascontiguousarray(wave, dtype=np.float32),
                "cursor": 0,
                "choke_group": choke_group
            })
        return True

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
            mixer_plugin = self._get_active_mixer_plugin()
            for track in self.project.tracks:
                if track.muted or (has_solo and not track.soloed):
                    if mixer_plugin:
                        mixer_plugin.update_track_peak(track.id, 0.0, 0.0)
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
                loop_len = self.project.loop_end_beat - self.project.loop_start_beat
                if loop_len > 0:
                    overshoot = end_beat - self.project.loop_end_beat
                    self.current_beat = self.project.loop_start_beat + (overshoot % loop_len)
                else:
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
            out = self._process_vst_effects(master_t, out)
            if hasattr(master_t, "plugins") and master_t.plugins:
                for plugin in master_t.plugins:
                    if getattr(plugin, "enabled", True):
                        try:
                            out = plugin.process(out, self.sample_rate)
                        except Exception as e:
                            pass

        # Application du volume Master et limitation douce (anti-saturation)
        out *= self.master_volume

        # Mesure des niveaux de crête et détection de distorsion / clipping (> 0 dBFS / > 1.0)
        if len(out) > 0:
            pk_l = float(np.max(np.abs(out[:, 0])))
            pk_r = float(np.max(np.abs(out[:, 1])))
            is_clip = bool(pk_l > 1.0 or pk_r > 1.0)
            now = time.time()
            with self._master_peak_lock:
                self._master_peak_l = max(self._master_peak_l * 0.82, pk_l)
                self._master_peak_r = max(self._master_peak_r * 0.82, pk_r)
                if is_clip:
                    self._master_clipped = True
                    self._master_clip_time = now
                elif self._master_clipped and (now - self._master_clip_time > 1.8):
                    self._master_clipped = False

        out = np.tanh(out)
        outdata[:] = out

    def get_master_peaks(self) -> tuple[float, float, bool]:
        """Retourne (peak_l, peak_r, is_clipped) pour le VU-mètre Master avec falloff doux."""
        with self._master_peak_lock:
            pk_l = self._master_peak_l
            pk_r = self._master_peak_r
            self._master_peak_l *= 0.88
            self._master_peak_r *= 0.88
            if self._master_peak_l < 1e-4:
                self._master_peak_l = 0.0
            if self._master_peak_r < 1e-4:
                self._master_peak_r = 0.0
            if self._master_clipped and (time.time() - self._master_clip_time > 1.8):
                self._master_clipped = False
            return pk_l, pk_r, self._master_clipped

    def reset_master_clip(self):
        """Réinitialise manuellement le voyant de distorsion/écrêtage."""
        with self._master_peak_lock:
            self._master_clipped = False

    def _process_vst_effects(self, track, track_buf):
        frames = len(track_buf)
        if hasattr(track, "insert_effects") and track.insert_effects and track_buf is not None:
            for fx_index, fx_path in enumerate(track.insert_effects):
                if not fx_path:
                    continue
                fx_plugin = self.get_effect_plugin(fx_path, f"track:{track.id}:effect:{fx_path}")
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
                        self.plugin_errors[track.id] = f"{track.name} : {e}"

        return track_buf

    def invalidate_track_cache(self, track_id: Optional[str] = None, trigger_async: bool = True):
        """Invalide le cache audio d'une piste ou de toutes les pistes lors d'une modification de note ou plugin."""
        with self._track_cache_lock:
            if track_id:
                self._track_audio_caches.pop(track_id, None)
            else:
                self._track_audio_caches.clear()
        if trigger_async:
            self._schedule_debounce_caching()

    def invalidate_all_track_caches(self, trigger_async: bool = False):
        """Vide l'intégralité du cache RAM ASIO-Guard."""
        with self._track_cache_lock:
            self._track_audio_caches.clear()
        if trigger_async:
            self._schedule_debounce_caching()

    def _schedule_debounce_caching(self, delay: float = 0.35):
        """Planifie un pré-rendu en arrière-plan avec debounce pour ne pas surcharger le processeur."""
        try:
            if getattr(self, "_debounce_timer", None) is not None:
                self._debounce_timer.cancel()
        except Exception:
            pass
        self._debounce_timer = threading.Timer(delay, self._ensure_tracks_cached_async)
        self._debounce_timer.daemon = True
        self._debounce_timer.start()

    def _ensure_tracks_cached_async(self, force: bool = False):
        """Lance le pré-calcul des caches de pistes en arrière-plan sans bloquer l'interface."""
        if not self._asio_guard_enabled or not self.project:
            return
        threading.Thread(
            target=self._ensure_tracks_cached,
            args=(force,),
            daemon=True,
            name="NovaDAW-AsioGuard-PreRender"
        ).start()

    def _compute_track_hash(self, track: Track, bpm: float, sample_rate: int) -> str:
        """Calcule une empreinte rapide des notes, clips et état d'instrument pour valider le cache ASIO-Guard."""
        parts = [track.id, str(track.plugin_path or ""), f"{bpm:.2f}", str(sample_rate)]

        # 1. État de l'instrument (direct ou routé vers une autre piste)
        target_track = None
        if getattr(track, "synth_route_track_id", None) and self.project:
            target_track = self.project.get_track(track.synth_route_track_id)
        native_inst = self.get_native_instrument(target_track) if target_track else self.get_native_instrument(track)
        if native_inst and hasattr(native_inst, "get_state"):
            try:
                parts.append(str(native_inst.get_state()))
            except Exception:
                pass

        # 2. Inserts natifs
        for p in getattr(track, "plugins", []):
            if hasattr(p, "get_state"):
                try:
                    parts.append(str(p.get_state()))
                except Exception:
                    pass
            elif hasattr(p, "plugin_type_id"):
                parts.append(str(getattr(p, "plugin_type_id", "")))

        # 3. Inserts VST externes
        for fx in getattr(track, "insert_effects", []):
            parts.append(str(fx))

        # 4. Clips et notes MIDI / audio
        for c in track.clips:
            if isinstance(c, MidiClip):
                parts.append(f"M:{c.id}:{c.start_beat:.3f}:{c.length_beats:.3f}:{len(c.notes)}")
                for n in c.notes:
                    parts.append(f"{n.pitch}:{n.start_beat:.3f}:{n.duration:.3f}:{n.velocity}")
            elif isinstance(c, AudioClip):
                parts.append(f"A:{c.id}:{c.start_beat:.3f}:{c.length_beats:.3f}:{getattr(c, 'file_path', '')}")

        import hashlib
        return hashlib.md5("".join(parts).encode("utf-8")).hexdigest()

    def _render_track_offline(self, track: Track, bpm: float, sample_rate: int, end_beat: float, progress_callback: Optional[Callable[[float], None]] = None) -> Optional[np.ndarray]:
        """
        Rendu offline haute vitesse d'une piste complète en mémoire RAM (ASIO-Guard).
        Élimine tout calcul d'oscillateur ou de filtre lors de la lecture continue.
        """
        beats_per_sec = bpm / 60.0
        total_sec = end_beat / beats_per_sec
        total_frames = max(1024, int(total_sec * sample_rate))
        buf = np.zeros((total_frames, 2), dtype=np.float32)

        # 1. Rendu des clips MIDI
        if track.track_type == "midi":
            target_track = None
            if getattr(track, "synth_route_track_id", None) and self.project:
                target_track = self.project.get_track(track.synth_route_track_id)
            native_inst = self.get_native_instrument(target_track) if target_track else self.get_native_instrument(track)

            if native_inst and hasattr(native_inst, "render_slice"):
                is_drum = (getattr(native_inst, "plugin_type_id", None) == "novadaw.drum_machine" or
                           getattr(track, "plugin_path", None) == "novadaw.drum_machine")
                release_tail_beats = 0.0 if is_drum else 2.0
                bus_idx = getattr(track, "synth_output_bus", None)

                # Pré-collecter toutes les notes MIDI de la piste une seule fois
                all_midi_notes = []
                for clip in track.clips:
                    if not isinstance(clip, MidiClip):
                        continue
                    cs = clip.start_beat
                    for note in clip.notes:
                        all_midi_notes.append(MidiNote(
                            pitch=note.pitch,
                            start_beat=cs + note.start_beat,
                            duration=note.duration,
                            velocity=note.velocity
                        ))
                all_midi_notes.sort(key=lambda n: n.start_beat)

                # Créer des instances isolées pour NovaSynth afin de ne jamais perturber les états de lecture temps réel
                iso_delay = None
                iso_reverb = None
                iso_svfs = None
                if getattr(native_inst, "plugin_type_id", None) == "novadaw.synth" and hasattr(native_inst, "_render_slice_internal"):
                    try:
                        from plugins.synth.synth_dsp import StereoPingPongDelay, StereoStudioReverb
                        iso_delay = StereoPingPongDelay(sample_rate=sample_rate)
                        if hasattr(native_inst, "delay"):
                            iso_delay.enabled = native_inst.delay.enabled
                            iso_delay.time_sec = native_inst.delay.time_sec
                            iso_delay.feedback = native_inst.delay.feedback
                            iso_delay.ping_pong = native_inst.delay.ping_pong
                            iso_delay.mix = native_inst.delay.mix
                        iso_reverb = StereoStudioReverb(sample_rate=sample_rate)
                        if hasattr(native_inst, "reverb"):
                            iso_reverb.enabled = native_inst.reverb.enabled
                            iso_reverb.room_size = native_inst.reverb.room_size
                            iso_reverb.damping = native_inst.reverb.damping
                            iso_reverb.width = native_inst.reverb.width
                            iso_reverb.mix = native_inst.reverb.mix
                        iso_svfs = {}
                    except Exception:
                        iso_delay = None

                # Traitement par blocs optimisés de 16384 échantillons (continuité fluide et rendu ultra-rapide)
                chunk_frames = 16384
                for f_idx in range(0, total_frames, chunk_frames):
                    n = min(chunk_frames, total_frames - f_idx)
                    sb = (f_idx / sample_rate) * beats_per_sec
                    eb = ((f_idx + n) / sample_rate) * beats_per_sec

                    if progress_callback and total_frames > 0:
                        try:
                            progress_callback(min(1.0, f_idx / total_frames))
                        except Exception:
                            pass

                    chunk_notes = [
                        note for note in all_midi_notes
                        if note.start_beat < eb and (note.start_beat + note.duration + release_tail_beats) > sb
                    ]

                    if chunk_notes:
                        try:
                            if iso_delay is not None:
                                rendered = native_inst._render_slice_internal(
                                    chunk_notes, sb, eb, beats_per_sec, n, sample_rate,
                                    bus_index=bus_idx,
                                    target_delay=iso_delay,
                                    target_reverb=iso_reverb,
                                    voice_svf_dict=iso_svfs,
                                    use_live_state=False
                                )
                            elif bus_idx is not None:
                                try:
                                    rendered = native_inst.render_slice(chunk_notes, sb, eb, beats_per_sec, n, sample_rate, bus_index=bus_idx)
                                except TypeError:
                                    rendered = native_inst.render_slice(chunk_notes, sb, eb, beats_per_sec, n, sample_rate)
                            else:
                                rendered = native_inst.render_slice(chunk_notes, sb, eb, beats_per_sec, n, sample_rate)

                            if rendered is not None and len(rendered) > 0:
                                buf[f_idx:f_idx + n] = rendered[:n]
                        except Exception as e:
                            print(f"[AudioEngine] Erreur pré-rendu tranche {track.name}: {e}")

        # 2. Rendu des clips Audio
        for clip in track.clips:
            if not isinstance(clip, AudioClip) or clip.audio_data is None:
                continue
            cs_b = clip.start_beat
            dur_sec = clip.length_beats / beats_per_sec
            clip_frames = int(dur_sec * sample_rate)
            dest_start = int((cs_b / beats_per_sec) * sample_rate)
            if dest_start < total_frames:
                src_samples = clip.audio_data
                to_copy = min(len(src_samples), clip_frames, total_frames - dest_start)
                if to_copy > 0:
                    if src_samples.ndim == 1:
                        buf[dest_start:dest_start + to_copy, 0] += src_samples[:to_copy] * clip.gain
                        buf[dest_start:dest_start + to_copy, 1] += src_samples[:to_copy] * clip.gain
                    else:
                        buf[dest_start:dest_start + to_copy] += src_samples[:to_copy, :2] * clip.gain

        # 3. Inserts de la piste (Égaliseur, Compresseur) avec état isolé
        if hasattr(track, "plugins") and track.plugins:
            for plugin in track.plugins:
                if getattr(plugin, "is_instrument", False):
                    continue
                if getattr(plugin, "enabled", True) and hasattr(plugin, "process"):
                    try:
                        if hasattr(plugin, "get_state") and hasattr(plugin, "set_state"):
                            iso_plugin = plugin.__class__()
                            iso_plugin.set_state(plugin.get_state())
                            buf = iso_plugin.process(buf, sample_rate)
                        elif hasattr(plugin, "clone"):
                            iso_plugin = plugin.clone()
                            buf = iso_plugin.process(buf, sample_rate)
                        else:
                            buf = plugin.process(buf, sample_rate)
                    except Exception:
                        pass

        # 4. Effets VST3 d'inserts (Pedalboard / VST)
        if hasattr(track, "insert_effects") and track.insert_effects:
            try:
                buf = self._process_vst_effects(track, buf)
            except Exception:
                pass

        if progress_callback:
            try:
                progress_callback(1.0)
            except Exception:
                pass

        return buf

    def _report_caching_progress(self, current: int, total: int, track_name: str, progress: float = 0.0):
        cb = self.caching_progress_callback
        if not cb:
            return
        try:
            cb(current, total, track_name, progress)
        except TypeError:
            try:
                cb(current, total, track_name)
            except Exception:
                pass
        except Exception:
            pass

    def _ensure_tracks_cached(self, force: bool = False):
        """Vérifie et pré-calcule le cache audio de toutes les pistes inactives pour la lecture (ASIO-Guard)."""
        if not self.project:
            return

        bpm = max(20.0, self.project.bpm)
        sample_rate = self.sample_rate

        max_beat = 16.0
        if self.project.loop_enabled and self.project.loop_end_beat > 0:
            max_beat = max(max_beat, self.project.loop_end_beat)
        for t in self.project.tracks:
            for c in t.clips:
                max_beat = max(max_beat, c.start_beat + c.length_beats)
        end_beat = max_beat + 4.0

        # Priorité de rendu : pistes en solo d'abord, puis pistes actives non muettes
        sorted_tracks = sorted(
            self.project.tracks,
            key=lambda t: (not t.soloed, t.muted)
        )
        total_tracks = max(1, len(sorted_tracks))

        for i, track in enumerate(sorted_tracks):
            # Ne pas pré-calculer les pistes armées pour l'enregistrement (faible latence temps réel)
            if getattr(track, "armed", False):
                continue
            thash = self._compute_track_hash(track, bpm, sample_rate)
            with self._track_cache_lock:
                cache = self._track_audio_caches.get(track.id)
                if (not force and cache is not None and cache.get("is_valid", False)
                    and cache.get("fully_rendered", False)
                    and cache.get("version_hash") == thash and cache.get("audio") is not None):
                    self._report_caching_progress(i + 1, total_tracks, track.name, (i + 1) / total_tracks)
                    continue

            self._report_caching_progress(i, total_tracks, track.name, i / total_tracks)

            def _track_subprogress(sub_pct: float, track_idx=i, track_name=track.name):
                overall = (track_idx + sub_pct) / total_tracks
                self._report_caching_progress(track_idx, total_tracks, track_name, overall)

            rendered = self._render_track_offline(track, bpm, sample_rate, end_beat, progress_callback=_track_subprogress)
            if rendered is not None:
                with self._track_cache_lock:
                    self._track_audio_caches[track.id] = {
                        "audio": rendered,
                        "bpm": bpm,
                        "sample_rate": sample_rate,
                        "total_beats": end_beat,
                        "version_hash": thash,
                        "is_valid": True,
                        "fully_rendered": True
                    }

            self._report_caching_progress(i + 1, total_tracks, track.name, (i + 1) / total_tracks)

        self._report_caching_progress(total_tracks, total_tracks, "Prêt", 1.0)

    def _get_cached_track_slice(self, track: Track, start_b: float, end_b: float, frames: int, bpm: float) -> Optional[np.ndarray]:
        """
        Récupère instantanément la tranche audio pré-calculée en RAM (ASIO-Guard).
        Temps d'exécution < 1 microseconde (0% CPU).
        """
        with self._track_cache_lock:
            cache = self._track_audio_caches.get(track.id)
            if cache is None or not cache.get("is_valid", False) or not cache.get("fully_rendered", False) or cache.get("audio") is None:
                return None

            beats_per_sec = bpm / 60.0
            s_start = int((start_b / beats_per_sec) * self.sample_rate)
            s_end = s_start + frames

            buf = cache["audio"]
            cache_sr = cache.get("sample_rate", self.sample_rate)

        if len(buf) == 0 or cache_sr != self.sample_rate:
            return None

        # Gestion de la boucle si activée
        if self.project and self.project.loop_enabled and self.project.loop_end_beat > self.project.loop_start_beat:
            loop_s_start = int((self.project.loop_start_beat / beats_per_sec) * self.sample_rate)
            loop_s_end = int((self.project.loop_end_beat / beats_per_sec) * self.sample_rate)
            loop_len = max(1, loop_s_end - loop_s_start)

            # Si start_b dépasse la boucle, replier start_b
            if s_start >= loop_s_end:
                s_start = loop_s_start + ((s_start - loop_s_start) % loop_len)
                s_end = s_start + frames

            if s_end <= loop_s_end and s_end <= len(buf):
                return buf[s_start:s_end].copy()
            else:
                boundary = min(loop_s_end, len(buf))
                p1 = max(0, boundary - s_start)
                out = np.zeros((frames, 2), dtype=np.float32)
                if p1 > 0:
                    out[:p1] = buf[s_start:boundary]
                rem = frames - p1
                if rem > 0 and loop_s_start + rem <= len(buf):
                    out[p1:] = buf[loop_s_start:loop_s_start + rem]
                return out
        else:
            if s_end <= len(buf):
                return buf[s_start:s_end].copy()
            elif s_start < len(buf):
                out = np.zeros((frames, 2), dtype=np.float32)
                p1 = len(buf) - s_start
                out[:p1] = buf[s_start:]
                return out
            else:
                return np.zeros((frames, 2), dtype=np.float32)

    def _render_track_slice(self, track: Track, start_b: float, end_b: float, frames: int, bpm: float) -> Optional[np.ndarray]:
        # 0. ASIO-Guard Cache Hit: Si la piste n'est pas armée pour l'enregistrement et dispose d'un cache valide
        if self._asio_guard_enabled and not getattr(track, "armed", False):
            cached_signal = self._get_cached_track_slice(track, start_b, end_b, frames, bpm)
            if cached_signal is not None:
                mixer_plugin = self._get_active_mixer_plugin()
                if mixer_plugin and len(cached_signal) > 0:
                    pk_l = float(np.max(np.abs(cached_signal[:, 0])))
                    pk_r = float(np.max(np.abs(cached_signal[:, 1])))
                    mixer_plugin.update_track_peak(track.id, pk_l, pk_r)
                return cached_signal

        track_buf = np.zeros((frames, 2), dtype=np.float32)
        beats_per_sec = bpm / 60.0

        if track.track_type == "midi":
            # 0. Si la piste est routée vers un instrument hébergé sur une autre piste (synth_route_track_id)
            target_track = None
            if getattr(track, "synth_route_track_id", None) and self.project:
                target_track = self.project.get_track(track.synth_route_track_id)

            # 1. Vérifier si la piste est routée vers un instrument virtuel natif (ex: Nova Drums VSTi, NovaSynth)
            native_inst = self.get_native_instrument(target_track) if target_track else self.get_native_instrument(track)
            rendered_by_native = False

            if native_inst and hasattr(native_inst, "render_slice"):
                clip_notes = []
                # Marge de queue de release ADSR adaptée (0 pour batterie one-shot, 1.5 pour synthé)
                is_drum = (getattr(native_inst, "plugin_type_id", None) == "novadaw.drum_machine" or
                           getattr(track, "plugin_path", None) == "novadaw.drum_machine")
                release_tail_beats = 0.0 if is_drum else 1.5
                for clip in track.clips:
                    if not isinstance(clip, MidiClip):
                        continue
                    clip_start = clip.start_beat
                    clip_end = clip_start + clip.length_beats
                    if (clip_end + release_tail_beats) < start_b or clip_start > end_b:
                        continue
                    for note in clip.notes:
                        abs_note_start = clip_start + note.start_beat
                        if abs_note_start < end_b and (abs_note_start + note.duration + release_tail_beats) > start_b:
                            clip_notes.append(MidiNote(
                                pitch=note.pitch,
                                start_beat=abs_note_start,
                                duration=note.duration,
                                velocity=note.velocity
                            ))
                try:
                    bus_idx = getattr(track, "synth_output_bus", None)
                    if bus_idx is not None:
                        try:
                            native_out = native_inst.render_slice(
                                clip_notes, start_b, end_b, beats_per_sec, frames, self.sample_rate, bus_index=bus_idx
                            )
                        except TypeError:
                            native_out = native_inst.render_slice(
                                clip_notes, start_b, end_b, beats_per_sec, frames, self.sample_rate
                            )
                    else:
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

                    midi_messages.sort(key=lambda event: event[1])
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
                    self.plugin_errors[track.id] = f"{track.name} : {e}"
                    rendered_by_vst = False

            # Le synthétiseur interne joue uniquement quand il est sélectionné.
            if not rendered_by_vst and not rendered_by_native and not track.plugin_path:
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

        # 2.bis Rendu des clips Audio présents sur la piste (pour une lecture universelle)
        for clip in track.clips:
            if not isinstance(clip, AudioClip) or clip.audio_data is None:
                continue

            clip_start = clip.start_beat
            clip_end = clip_start + clip.length_beats
            if clip_end < start_b or clip_start > end_b:
                continue

            # Position dans les samples du clip avec prise en compte du décalage de source
            clip_samples_len = len(clip.audio_data)
            offset_beats = float(getattr(clip, "source_offset_beats", 0.0))
            sample_offset = int(((start_b - clip_start + offset_beats) / beats_per_sec) * self.sample_rate)

            buf_start = 0
            clip_read_start = sample_offset
            if clip_read_start < 0:
                buf_start = -clip_read_start
                clip_read_start = 0

            max_clip_samples = int(((offset_beats + clip.length_beats) / beats_per_sec) * self.sample_rate)
            readable_end = min(clip_samples_len, max_clip_samples)

            if clip_read_start < readable_end and buf_start < frames:
                to_copy = min(readable_end - clip_read_start, frames - buf_start)
                if to_copy > 0:
                    samples = clip.audio_data[clip_read_start:clip_read_start + to_copy] * clip.gain
                    if samples.ndim == 1:
                        track_buf[buf_start:buf_start + to_copy, 0] += samples
                        track_buf[buf_start:buf_start + to_copy, 1] += samples
                    else:
                        track_buf[buf_start:buf_start + to_copy] += samples[:, :2]

        track_buf = self._process_vst_effects(track, track_buf)

        # 4. Traitement de la pile de plugins modulaires de la piste (Égaliseur, Compresseur, etc.)
        if hasattr(track, "plugins") and track.plugins and track_buf is not None:
            for plugin in track.plugins:
                if track.track_type == "midi" and (plugin is native_inst or getattr(plugin, "is_instrument", False)):
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
                out_chunk = self._process_vst_effects(master_t, out_chunk)
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

"""
core/audio_engine.py - Moteur audio temps réel, synthétiseur polyphonique et mixeur
"""
import os
import sys
import copy
import hashlib
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Optional, List, Dict, Callable, Any, Iterator
import numpy as np
try:
    import sounddevice as sd
    HAS_SOUNDDEVICE = True
except (ImportError, OSError):
    sd = None
    HAS_SOUNDDEVICE = False
import soundfile as sf
from core.project import Project, Track, MidiClip, AudioClip, MidiNote
from plugins.base import BasePlugin, AutomationProcessContext



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

        self._wasapi_exclusive: bool = True
        self._streaming_mode: bool = False
        self._active_stream_mode: str = "shared"
        self._effective_stream_block_size: int = 256
        self._suspended_in_background: bool = False
        loaded_asio_guard: bool = True
        loaded_record_offset: int = 0
        # Configuration automatique universelle du matériel audio
        try:
            from core.hardware_manager import hardware_manager
            audio_cfg = hardware_manager.settings.get("audio", {})
            self._wasapi_exclusive = bool(audio_cfg.get("wasapi_exclusive", True))
            self._streaming_mode = bool(audio_cfg.get("streaming_mode", False))
            loaded_asio_guard = bool(audio_cfg.get("asio_guard_enabled", True))
            if os.environ.get("NOVADAW_SILENT_TESTS") != "1":
                loaded_record_offset = int(audio_cfg.get("record_offset_samples", 0))
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

            extra = None
            if HAS_SOUNDDEVICE and hardware_manager.is_wasapi_device(self.output_device) and hasattr(sd, "WasapiSettings"):
                try:
                    if self._streaming_mode:
                        extra = sd.WasapiSettings(exclusive=False, auto_convert=True)
                    elif self._wasapi_exclusive:
                        extra = sd.WasapiSettings(exclusive=True)
                except Exception:
                    extra = None
            optimal_sr = hardware_manager.get_optimal_sample_rate(self.output_device, extra_settings=extra)
            self.sample_rate = int(audio_cfg.get("sample_rate", optimal_sr)) if sample_rate is None else sample_rate
            optimal_buf = hardware_manager.get_optimal_buffer_size(self.output_device, self.sample_rate)
            self.block_size = int(audio_cfg.get("buffer_size", optimal_buf)) if block_size is None else block_size
        except Exception:
            self.sample_rate = sample_rate if sample_rate is not None else 44100
            self.block_size = block_size if block_size is not None else 512
            self.output_device = output_device
            self.input_device = input_device
        self._effective_stream_block_size = self.block_size

        self.project: Optional[Project] = None

        self.is_playing = False
        self.current_beat = 0.0
        self.master_volume = 0.9

        # Système de Pré-rendu Audio et Cache ASIO-Guard en RAM (Hybrid Audio Engine)
        self._track_audio_caches: Dict[str, Dict[str, Any]] = {}
        self._track_dry_caches: Dict[str, Dict[str, Any]] = {}
        self._track_cache_lock = threading.RLock()
        self._cache_done_cond = threading.Condition(self._track_cache_lock)
        self._asio_guard_enabled: bool = loaded_asio_guard
        # One producer coordinator for all tracks; invalidation makes in-flight buffers obsolete.
        self._cache_job_lock = threading.Lock()
        self._cache_render_lock = threading.Lock()
        self._cache_worker = None
        self._cache_request_pending = False
        self._cache_force_requested = False
        self._cache_requested_seq: int = 0
        self._cache_completed_seq: int = 0
        self._cache_generation = 0
        self._state_generation: int = 0
        self._track_generations: Dict[str, int] = {}
        self._track_fx_generations: Dict[str, int] = {}
        self._track_idle_state: Dict[str, Dict[str, Any]] = {}
        self._isolated_fx_clones: Dict[tuple[int, str], tuple[int, Any]] = {}
        self._cache_closed = False
        self._export_lock = threading.Lock()

        # Enregistrement audio et MIDI
        self.is_recording = False
        self.recording_start_beat = 0.0
        self._input_stream: Optional[sd.InputStream] = None
        self._recorded_audio_blocks: List[np.ndarray] = []
        self._recorded_midi_notes: List[MidiNote] = []
        self._record_lock = threading.Lock()
        self.record_latency_compensation_samples: int = loaded_record_offset
        self._record_sample_rate: int = self.sample_rate

        # Monitoring audio live (entrée micro -> effets de piste -> sortie temps réel)
        self._monitor_lock = threading.Lock()
        self._monitor_enabled: bool = False
        self._monitor_chunks: List[np.ndarray] = []
        self._monitor_buffered_samples: int = 0

        # Instances VST3 chargées par piste (track_id -> plugin instance)
        self.plugin_errors = {}
        self.track_plugins: Dict[str, Any] = {}

        # File d'attente pour les notes de prévisualisation (clic sur piano roll)
        self._preview_lock = threading.Lock()
        self._preview_buffers: List[Dict] = []

        # Streaming temps réel continu pour les instruments VST (maintien infini + queue de release + aperçu GUI VST)
        self._live_stream_lock = threading.Lock()
        self._live_stream_chunks: List[Dict[str, Any]] = []
        self._live_midi_lock = threading.Lock()
        self._live_midi_queue: Dict[str, List[tuple[bytes, float]]] = {}
        self._vst_midi_channels: Dict[str, int] = {}
        self._live_scheduled_note_offs: List[tuple[float, str, int, int]] = []
        self._live_active_notes: Dict[tuple[str, int], Dict[str, Any]] = {}
        self._live_active_track: Optional[Track] = None
        self._live_tail_until: float = 0.0
        self._live_last_audible_time: float = 0.0
        self._live_direct_active: bool = False
        self._cb_out_buf: Optional[np.ndarray] = None
        self._cb_track_bufs: Dict[str, np.ndarray] = {}
        self._cb_mono_scratch: Optional[np.ndarray] = None
        self._cb_peak_slots: Dict[str, List[float]] = {}
        self._cb_active_peak_tids: List[str] = []
        self._cb_auto_ctx_cache: Dict[str, AutomationProcessContext] = {}
        self._plugin_mgr: Optional[Any] = None
        self._live_vst_buf: Optional[np.ndarray] = None
        self._live_vst_event = threading.Event()
        self._live_vst_thread: Optional[threading.Thread] = None

        # Streaming temps réel continu bloc-à-bloc pour instruments natifs & synthé interne
        self._live_native_lock = threading.Lock()
        self._live_native_notes: List[Dict[str, Any]] = []
        self._live_native_sample_pos: int = 0
        self._live_native_tail_until: Dict[str, float] = {}
        self._live_native_tracks: Dict[str, Track] = {}
        self._live_native_contexts: Dict[tuple[str, str], Any] = {}
        self._next_live_note_id: int = 1
        self._playback_live_step: Optional[tuple[float, float, Dict[str, List[Dict[str, Any]]]]] = None

        # Mesure des niveaux de crête et distorsion Master (VU-mètre)
        self._master_peak_l: float = 0.0
        self._master_peak_r: float = 0.0
        self._master_clipped: bool = False
        self._master_clip_time: float = 0.0
        self._master_peak_lock = threading.Lock()

        # File d'attente thread-safe pour les écritures d'automatisation pilotées par plugins (ex: Nova Vocal Glide)
        self._automation_write_lock = threading.Lock()
        self._pending_automation_writes: List[tuple[str, str, float, float, str]] = []
        self._live_automation_values: Dict[str, Dict[str, float]] = {}

        # Tête de lecture et notification UI
        self.playhead_callback: Optional[Callable[[float], None]] = None
        self.caching_progress_callback: Optional[Callable[[int, int, str], None]] = None
        self._stream: Optional[sd.OutputStream] = None

        self._start_stream()

    @property
    def streaming_mode(self) -> bool:
        """Indique si le mode Streaming & Capture Vidéo sans glitch est actif."""
        return self._streaming_mode

    @property
    def effective_block_size(self) -> int:
        """Retourne la taille réelle de bloc utilisée par le flux actif (sans altérer self.block_size studio)."""
        return self._effective_stream_block_size if self._streaming_mode else self.block_size

    @property
    def active_stream_mode(self) -> str:
        """Retourne le mode réel du flux ouvert ('streaming', 'wasapi_exclusive', 'shared')."""
        return self._active_stream_mode

    def configure_device(self, output_device: Optional[int] = None, input_device: Optional[int] = None, sample_rate: Optional[int] = None, block_size: Optional[int] = None, buffer_size: Optional[int] = None, record_offset_samples: Optional[int] = None, asio_guard: Optional[bool] = None, wasapi_exclusive: Optional[bool] = None, streaming_mode: Optional[bool] = None) -> bool:
        """Modifie le périphérique audio, la fréquence d'échantillonnage ou la taille du buffer et redémarre le flux."""
        prev_sr = self.sample_rate
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
        if wasapi_exclusive is not None:
            self._wasapi_exclusive = bool(wasapi_exclusive)
        if streaming_mode is not None:
            self._streaming_mode = bool(streaming_mode)
        self._suspended_in_background = False
        self._start_stream()
        if self.sample_rate != prev_sr and self.project is not None:
            self.invalidate_all_track_caches(trigger_async=self._asio_guard_enabled)
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

    def _prime_startup_prefix(self, prefix_frames: int = 8192) -> None:
        """Pré-remplit de façon synchrone les premiers échantillons (beat 0) pour garantir 0 décrochage au démarrage immédiat."""
        project = self.project
        if project is None or not self._asio_guard_enabled or self._cache_closed:
            return
        if "_iter_track_offline" in self.__dict__:
            return
        bpm = max(20.0, project.bpm)
        sample_rate = self.sample_rate
        max_beat = max(16.0, project.loop_end_beat if project.loop_enabled else 0.0)
        for track in project.tracks:
            for clip in track.clips:
                max_beat = max(max_beat, clip.start_beat + clip.length_beats)
        end_beat = max_beat + 4.0
        for track in project.tracks:
            if track.plugin_path == "novadaw.piano":
                piano = self.get_native_instrument(track)
                if piano is not None:
                    tail_beats = piano.get_release_tail_sec() * bpm / 60.0
                    for clip in track.clips:
                        if isinstance(clip, MidiClip):
                            end_beat = max(end_beat, clip.start_beat + clip.length_beats + tail_beats)
        total_frames = max(1024, int(end_beat / (bpm / 60.0) * sample_rate))
        n_pref = min(prefix_frames, total_frames)

        for track in project.tracks:
            if getattr(track, "armed", False):
                continue
            snapshot = self._snapshot_cache_track(track)
            if snapshot is None:
                continue
            tid = track.id
            gen_hash = self._compute_track_generator_hash(track, bpm, sample_rate)
            thash = self._compute_track_hash(track, bpm, sample_rate, gen_hash=gen_hash)
            inst_sig = self._fast_inst_signature(track)
            pref_buf = np.zeros((total_frames, 2), dtype=np.float32)
            self._render_generator_range_into(
                snapshot, pref_buf, 0, n_pref, bpm, sample_rate, end_beat,
                abort_check=lambda: False,
                on_chunk_ready=None,
            )
            with self._track_cache_lock:
                tgen = self._track_generations.get(tid, 0)
                fgen = self._track_fx_generations.get(tid, 0)
                includes = bool(getattr(snapshot, "_includes_inserts", False))
                is_done = n_pref >= total_frames
                self._track_dry_caches[tid] = {
                    "audio": pref_buf,
                    "bpm": bpm,
                    "sample_rate": sample_rate,
                    "total_beats": end_beat,
                    "gen_hash": gen_hash,
                    "track_gen": tgen,
                    "inst_sig": inst_sig,
                    "valid_start_frame": 0,
                    "rendered_frames": n_pref,
                    "fully_rendered": is_done,
                    "is_valid": True,
                }
                self._track_audio_caches[tid] = {
                    "audio": pref_buf,
                    "bpm": bpm,
                    "sample_rate": sample_rate,
                    "total_beats": end_beat,
                    "version_hash": thash,
                    "track_gen": tgen,
                    "fx_gen": fgen,
                    "inst_sig": inst_sig,
                    "includes_inserts": includes,
                    "valid_start_frame": 0,
                    "rendered_frames": n_pref,
                    "fully_rendered": is_done,
                    "is_valid": True,
                }

    def set_project(self, project: Project):
        self._cache_closed = False
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
        if self._asio_guard_enabled and "_ensure_tracks_cached" not in self.__dict__ and "_iter_track_offline" not in self.__dict__:
            self._prime_startup_prefix(prefix_frames=16384)
        self._ensure_tracks_cached_async()

    def _start_stream(self):
        self._cache_closed = False
        global _global_audio_engine
        _global_audio_engine = self
        from core.hardware_manager import hardware_manager

        if os.environ.get("NOVADAW_SILENT_TESTS") == "1":
            self._effective_stream_block_size = (
                hardware_manager.get_streaming_block_size(self.sample_rate, self.block_size)
                if self._streaming_mode
                else self.block_size
            )
            self._active_stream_mode = (
                "streaming"
                if self._streaming_mode
                else ("wasapi_exclusive" if self._wasapi_exclusive else "shared")
            )
            self._stream = None
            return

        target_device = self.output_device
        if target_device is not None and target_device < 0:
            target_device = None

        opened = False

        # --- CHEMIN 1 : MODE STREAMING & CAPTURE VIDÉO SANS GLITCH (NVIDIA App / OBS) ---
        if self._streaming_mode:
            stream_device = hardware_manager.resolve_wasapi_output_device(target_device)
            is_wasapi_stream = hardware_manager.is_wasapi_device(stream_device)
            wasapi_shared_auto = None
            if is_wasapi_stream and HAS_SOUNDDEVICE and hasattr(sd, "WasapiSettings"):
                try:
                    wasapi_shared_auto = sd.WasapiSettings(exclusive=False, auto_convert=True)
                except Exception:
                    wasapi_shared_auto = None

            extra_modes = [wasapi_shared_auto, None] if wasapi_shared_auto is not None else [None]
            for extra in extra_modes:
                valid_sr = hardware_manager.get_optimal_sample_rate(stream_device, extra_settings=extra)
                candidate_srs = [self.sample_rate, valid_sr, 48000, 44100]
                seen = set()
                candidate_srs = [x for x in candidate_srs if not (x in seen or seen.add(x))]

                for sr in candidate_srs:
                    aligned_blk = hardware_manager.get_streaming_block_size(sr, self.block_size)
                    block_trials = [aligned_blk, self.block_size] if aligned_blk != self.block_size else [aligned_blk]
                    for blk in block_trials:
                        try:
                            if stream_device is not None:
                                if extra is not None:
                                    sd.check_output_settings(
                                        device=stream_device,
                                        samplerate=sr,
                                        channels=2,
                                        dtype="float32",
                                        extra_settings=extra,
                                    )
                                else:
                                    sd.check_output_settings(device=stream_device, samplerate=sr)
                            else:
                                sd.check_output_settings(samplerate=sr)

                            kwargs = {
                                "samplerate": sr,
                                "blocksize": blk,
                                "channels": 2,
                                "dtype": "float32",
                                "latency": hardware_manager.STREAMING_HOST_LATENCY_SEC,
                                "callback": self._audio_callback,
                            }
                            if stream_device is not None:
                                kwargs["device"] = stream_device
                            if extra is not None:
                                kwargs["extra_settings"] = extra

                            self._stream = sd.OutputStream(**kwargs)
                            self._stream.start()
                            self.sample_rate = sr
                            self._effective_stream_block_size = blk if blk > 0 else aligned_blk
                            self._active_stream_mode = "streaming"
                            opened = True
                            print(
                                f"[AudioEngine] Flux audio démarré (Mode Streaming sans glitch, "
                                f"device={stream_device}, sr={sr}, block={blk}, latency={self._stream.latency*1000:.1f}ms)"
                            )
                            break
                        except Exception:
                            continue
                    if opened:
                        break
                if opened:
                    break

        # --- CHEMIN 2 : MODE STUDIO BASSE LATENCE (Comportement d'origine 100% préservé) ---
        else:
            is_wasapi = hardware_manager.is_wasapi_device(target_device)
            wasapi_excl_settings = None
            if is_wasapi and self._wasapi_exclusive and HAS_SOUNDDEVICE and hasattr(sd, "WasapiSettings"):
                try:
                    wasapi_excl_settings = sd.WasapiSettings(exclusive=True)
                except Exception:
                    wasapi_excl_settings = None

            # Préparer les essais d'ouverture : WASAPI Exclusif d'abord (si actif), puis Partagé en repli
            extra_modes = [wasapi_excl_settings, None] if wasapi_excl_settings is not None else [None]

            for extra in extra_modes:
                valid_sr = hardware_manager.get_optimal_sample_rate(target_device, extra_settings=extra)
                candidate_srs = [self.sample_rate, valid_sr, 44100, 48000] if extra is None else [valid_sr, self.sample_rate, 44100, 48000]
                seen = set()
                candidate_srs = [x for x in candidate_srs if not (x in seen or seen.add(x))]
                block_trials = [self.block_size, 0] if extra is not None else [self.block_size]

                for sr in candidate_srs:
                    for blk in block_trials:
                        try:
                            if target_device is not None:
                                if extra is not None:
                                    sd.check_output_settings(device=target_device, samplerate=sr, channels=2, dtype="float32", extra_settings=extra)
                                else:
                                    sd.check_output_settings(device=target_device, samplerate=sr)
                            else:
                                sd.check_output_settings(samplerate=sr)

                            kwargs = {
                                "samplerate": sr,
                                "blocksize": blk,
                                "channels": 2,
                                "dtype": "float32",
                                "latency": "low",
                                "callback": self._audio_callback,
                            }
                            if target_device is not None:
                                kwargs["device"] = target_device
                            if extra is not None:
                                kwargs["extra_settings"] = extra

                            self._stream = sd.OutputStream(**kwargs)
                            self._stream.start()
                            self.sample_rate = sr
                            self._effective_stream_block_size = blk if blk > 0 else self.block_size
                            self._active_stream_mode = "wasapi_exclusive" if extra is not None else "shared"
                            opened = True
                            mode_lbl = "WASAPI Exclusif" if extra is not None else "Standard"
                            print(f"[AudioEngine] Flux audio démarré ({mode_lbl}, device={target_device}, sr={sr}, block={blk})")
                            break
                        except Exception:
                            continue
                    if opened:
                        break
                if opened:
                    break

        # Si le périphérique a échoué (ex: débranché ou invalide), déclencher l'auto-détection universelle
        if not opened:
            print("[AudioEngine] Repli vers auto-détection universelle du matériel...")
            try:
                new_cfg = hardware_manager.auto_configure_audio(force=True)
                new_dev = new_cfg.get("output_device_index")
                new_sr = new_cfg.get("sample_rate", 44100)
                new_buf = (
                    hardware_manager.get_streaming_block_size(new_sr, new_cfg.get("buffer_size", 128))
                    if self._streaming_mode
                    else new_cfg.get("buffer_size", 128)
                )

                kwargs = {
                    "samplerate": new_sr,
                    "blocksize": new_buf,
                    "channels": 2,
                    "dtype": "float32",
                    "latency": hardware_manager.STREAMING_HOST_LATENCY_SEC if self._streaming_mode else "low",
                    "callback": self._audio_callback
                }
                if new_dev is not None and new_dev >= 0:
                    kwargs["device"] = new_dev

                self._stream = sd.OutputStream(**kwargs)
                self._stream.start()
                self.output_device = new_dev
                self.sample_rate = new_sr
                if not self._streaming_mode:
                    self.block_size = new_buf
                self._effective_stream_block_size = new_buf
                self._active_stream_mode = "streaming" if self._streaming_mode else "shared"
                opened = True
                print(f"[AudioEngine] Flux audio rétabli avec succès via auto-détection ({new_sr} Hz) !")
            except Exception as e_fallback:
                print(f"[AudioEngine] Erreur critique d'initialisation du périphérique de secours: {e_fallback}")
                self._stream = None

        if not opened:
            print("[AudioEngine] Avertissement : Impossible d'ouvrir un flux audio physique.")
            self._stream = None
        else:
            self._ensure_live_vst_streamer()

    def suspend_for_background(self) -> bool:
        """Libère le flux audio matériel lorsque NovaDAW passe en arrière-plan (sauf lecture/enregistrement/streaming)."""
        if self._streaming_mode or self.is_playing or self.is_recording or self._monitor_enabled:
            return False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            self._suspended_in_background = True
            return True
        return False

    def resume_from_background(self) -> bool:
        """Réactive le flux audio matériel lorsque NovaDAW revient au premier plan."""
        if not self._suspended_in_background:
            return False
        self._suspended_in_background = False
        if self._stream is None and os.environ.get("NOVADAW_SILENT_TESTS") != "1":
            self._start_stream()
        return self._stream is not None or os.environ.get("NOVADAW_SILENT_TESTS") == "1"

    def close(self):
        with self._cache_job_lock:
            self._cache_closed = True
            self._cache_request_pending = False
        with self._live_native_lock:
            self._live_native_notes.clear()
            self._live_native_tail_until.clear()
            self._live_native_tracks.clear()
            self._live_native_contexts.clear()
        self._live_vst_event.set()
        with self._track_cache_lock:
            self._cache_generation += 1
            self._state_generation += 1
            self._track_audio_caches.clear()
            self._track_dry_caches.clear()
            self._cache_done_cond.notify_all()
        self._track_idle_state.clear()
        self._isolated_fx_clones.clear()
        timer = getattr(self, "_debounce_timer", None)
        if timer is not None:
            timer.cancel()
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


    def _close_input_stream_if_idle(self) -> None:
        """Ferme le flux d'entrée physique uniquement s'il n'est utilisé ni en enregistrement ni en monitoring."""
        if self.is_recording or self._monitor_enabled:
            return
        if self._input_stream:
            try:
                self._input_stream.stop()
                self._input_stream.close()
            except Exception:
                pass
            self._input_stream = None

    def _ensure_input_stream_open(self) -> None:
        """Ouvre le flux de capture micro si nécessaire (partagé entre enregistrement et monitoring live)."""
        if self._input_stream is not None:
            return

        if os.environ.get("NOVADAW_SILENT_TESTS") == "1":
            try:
                self._input_stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    blocksize=self.block_size,
                    channels=2,
                    callback=self._record_callback
                )
                self._input_stream.start()
                self._record_sample_rate = self.sample_rate
            except Exception:
                self._input_stream = None
            return

        try:
            in_dev = self.input_device
            if in_dev is None or in_dev < 0:
                from core.hardware_manager import hardware_manager
                in_dev = hardware_manager.get_default_input_device_index()

            dev_default_sr = self.sample_rate
            in_channels = 1
            try:
                dev_info = sd.query_devices(in_dev, kind="input") if in_dev is not None else sd.query_devices(kind="input")
                max_in = dev_info.get("max_input_channels", 1)
                in_channels = min(2, max(1, max_in))
                dev_default_sr = int(dev_info.get("default_samplerate", self.sample_rate))
            except Exception:
                pass

            opened = False
            candidate_srs = [self.sample_rate, dev_default_sr, 48000, 44100]
            seen_srs = set()
            candidate_srs = [x for x in candidate_srs if not (x in seen_srs or seen_srs.add(x))]
            channel_trials = [in_channels] if in_channels == 1 else [in_channels, 1]

            for ch in channel_trials:
                for sr in candidate_srs:
                    try:
                        kwargs = {
                            "samplerate": sr,
                            "blocksize": self.block_size,
                            "channels": ch,
                            "dtype": "float32",
                            "callback": self._record_callback
                        }
                        if in_dev is not None and in_dev >= 0:
                            kwargs["device"] = in_dev

                        self._input_stream = sd.InputStream(**kwargs)
                        self._input_stream.start()
                        self._record_sample_rate = sr
                        opened = True
                        print(f"[AudioEngine] Capture micro démarrée avec succès (device={in_dev}, sr={sr}, channels={ch})")
                        break
                    except Exception:
                        continue
                if opened:
                    break

            if not opened:
                try:
                    self._input_stream = sd.InputStream(
                        samplerate=dev_default_sr,
                        blocksize=self.block_size,
                        channels=1,
                        dtype="float32",
                        callback=self._record_callback
                    )
                    self._input_stream.start()
                    self._record_sample_rate = dev_default_sr
                    opened = True
                    print("[AudioEngine] Capture micro démarrée via repli système par défaut")
                except Exception as e_final:
                    print(f"[AudioEngine] Impossible d'ouvrir le flux de capture d'entrée : {e_final}")
                    self._input_stream = None
        except Exception as e:
            print(f"[AudioEngine] Erreur critique initialisation micro : {e}")
            self._input_stream = None

    def sync_project_monitoring(self) -> bool:
        """Active ou désactive le flux d'entrée live selon l'état `monitoring` des pistes audio du projet."""
        active = False
        if self.project and hasattr(self.project, "tracks"):
            active = any(
                getattr(t, "track_type", "") == "audio"
                and getattr(t, "monitoring", False)
                and not getattr(t, "muted", False)
                for t in self.project.tracks
            )
        self._monitor_enabled = active
        if active:
            self._ensure_input_stream_open()
        else:
            with self._monitor_lock:
                self._monitor_chunks.clear()
                self._monitor_buffered_samples = 0
            self._close_input_stream_if_idle()
        return active

    def feed_live_input_block(self, indata: np.ndarray, input_sr: Optional[int] = None) -> None:
        """Injecte un bloc audio micro stéréo dans le ring buffer du monitoring live."""
        if indata is None or len(indata) == 0:
            return
        if indata.ndim == 1:
            stereo = np.column_stack((indata, indata)).astype(np.float32)
        elif indata.shape[1] == 1:
            stereo = np.column_stack((indata[:, 0], indata[:, 0])).astype(np.float32)
        else:
            stereo = np.ascontiguousarray(indata[:, :2], dtype=np.float32)

        src_sr = int(input_sr or self.sample_rate)
        if src_sr != self.sample_rate and len(stereo) > 1:
            new_len = max(1, int(round(len(stereo) * (self.sample_rate / float(src_sr)))))
            idx_orig = np.linspace(0.0, len(stereo) - 1, new_len)
            l = np.interp(idx_orig, np.arange(len(stereo)), stereo[:, 0]).astype(np.float32)
            r = np.interp(idx_orig, np.arange(len(stereo)), stereo[:, 1]).astype(np.float32)
            stereo = np.column_stack((l, r))

        max_cap = max(4096, self.block_size * 4)
        with self._monitor_lock:
            self._monitor_chunks.append(stereo)
            self._monitor_buffered_samples += len(stereo)
            while self._monitor_buffered_samples > max_cap and len(self._monitor_chunks) > 1:
                dropped = self._monitor_chunks.pop(0)
                self._monitor_buffered_samples -= len(dropped)

    def _consume_monitor_block(self, frames: int) -> Optional[np.ndarray]:
        """Consomme jusqu'à `frames` échantillons depuis le buffer de monitoring live."""
        with self._monitor_lock:
            if not self._monitor_chunks:
                return None
            out = np.zeros((frames, 2), dtype=np.float32)
            filled = 0
            while filled < frames and self._monitor_chunks:
                head = self._monitor_chunks[0]
                take = min(frames - filled, len(head))
                out[filled:filled + take] = head[:take]
                filled += take
                self._monitor_buffered_samples -= take
                if take < len(head):
                    self._monitor_chunks[0] = head[take:]
                else:
                    self._monitor_chunks.pop(0)
            return out

    def start_recording(self, start_beat: float):
        """Démarre la capture audio en direct et initialise l'enregistrement."""
        self.is_recording = True
        self.recording_start_beat = max(0.0, float(start_beat))
        with self._record_lock:
            self._recorded_audio_blocks.clear()
            self._recorded_midi_notes.clear()

        if self._input_stream is not None and not self._monitor_enabled:
            try:
                self._input_stream.stop()
                self._input_stream.close()
            except Exception:
                pass
            self._input_stream = None

        self._ensure_input_stream_open()

    def _record_callback(self, indata, frames, time_info, status):
        """Callback temps réel de capture audio du microphone/ligne"""
        if self.is_recording:
            with self._record_lock:
                self._recorded_audio_blocks.append(indata.copy())
        if self._monitor_enabled:
            self.feed_live_input_block(indata, getattr(self, "_record_sample_rate", self.sample_rate))

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
        self._close_input_stream_if_idle()

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

    def _reset_track_insert_fx(self, track: Optional[Track]) -> None:
        """Réinitialise uniquement les effets d'insert d'une piste (ex: transition mute / silence)."""
        if track is None:
            return
        native_inst = self.get_native_instrument(track)
        for p in getattr(track, "plugins", []):
            if self._is_non_insert_plugin(p, track, native_inst):
                continue
            if getattr(p, "plugin_type_id", None) == "novadaw.vocal_glide" and getattr(p, "idle_behavior", "") == "hold":
                continue
            if hasattr(p, "reset"):
                try:
                    p.reset()
                except Exception:
                    pass

    def _reset_all_plugins(self):
        """Réinitialise les mémoires des filtres et compresseurs lors d'un arrêt ou repositionnement"""
        with self._live_native_lock:
            self._live_native_contexts.clear()
        self._track_idle_state.clear()
        self._isolated_fx_clones.clear()
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

    def _ensure_live_vst_streamer(self):
        """Démarre le thread daemon de streaming VST temps réel si un flux audio est actif."""
        if os.environ.get("NOVADAW_SILENT_TESTS") == "1":
            return
        if self._live_vst_thread is not None and self._live_vst_thread.is_alive():
            return
        self._live_vst_thread = threading.Thread(
            target=self._live_vst_loop,
            daemon=True,
            name="NovaDAW-LiveVST-Streamer"
        )
        self._live_vst_thread.start()

    def start_live_note(self, pitch: int, velocity: int = 100, track: Optional[Track] = None):
        """
        Démarre immédiatement une note en direct (Note-On maintenu sans limite de temps).
        Pour les VST externes (ex: Omnisphere), streame les blocs temps réel en arrière-plan
        tant que la touche est maintenue puis laisse résonner toute la queue de release/réverb.
        """
        stream_active = bool(
            self._stream is not None
            and getattr(self._stream, "active", False)
            and os.environ.get("NOVADAW_SILENT_TESTS") != "1"
        )
        if not stream_active:
            self.preview_note(pitch, duration_sec=0.35, velocity=velocity, track=track)
            return

        target_track = track
        if track and getattr(track, "synth_route_track_id", None) and self.project:
            routed = self.project.get_track(track.synth_route_track_id)
            if routed:
                target_track = routed

        native_inst = self.get_native_instrument(target_track) if target_track else None
        if native_inst is None and target_track and target_track.plugin_path:
            vst_plugin = self.get_track_plugin(target_track)
            if self._can_render_vst_offline(vst_plugin):
                use_direct = hasattr(vst_plugin, "render_live_block") and getattr(vst_plugin, "_shm", None) is not None
                if not use_direct:
                    self._ensure_live_vst_streamer()
                now = time.time()
                vel = max(1, min(127, int(velocity)))
                with self._live_midi_lock:
                    for _when, tid, old_pitch, old_channel in self._live_scheduled_note_offs:
                        if tid == target_track.id and old_pitch == pitch:
                            self._live_midi_queue.setdefault(tid, []).append(
                                (bytes([0x80 | old_channel, pitch, 0]), 0.0))
                    self._live_scheduled_note_offs = [
                        item for item in self._live_scheduled_note_offs
                        if not (item[1] == target_track.id and item[2] == pitch)
                    ]
                    q = self._live_midi_queue.setdefault(target_track.id, [])
                    if (target_track.id, pitch) in self._live_active_notes:
                        q.append((bytes([0x80 | self._live_active_notes[(target_track.id, pitch)].get("channel", 0), pitch, 0]), 0.0))
                    self._live_active_notes[(target_track.id, pitch)] = {
                        "channel": self._midi_status(0, track),
                        "press_time": now,
                        "start_beat": self.current_beat,
                        "velocity": vel,
                        "record_track": track,
                    }
                    q.append((bytes([self._midi_status(0x90, track), pitch, vel]), 0.0))
                    self._live_active_track = target_track
                    self._live_direct_active = use_direct
                    self._live_tail_until = max(self._live_tail_until, now + 4.0)
                if not use_direct:
                    self._live_vst_event.set()
                return

        # Instruments natifs (Nova Guitar, NovaSynth, Nova Drums) ou SynthVoice par défaut :
        # enregistrement instantané (< 0.05 ms) pour rendu temps réel bloc-à-bloc dans _audio_callback.
        if native_inst is None and target_track and target_track.plugin_path:
            return

        is_drum = bool(
            (native_inst and getattr(native_inst, "plugin_type_id", None) == "novadaw.drum_machine")
            or (target_track and getattr(target_track, "plugin_path", None) == "novadaw.drum_machine")
        )
        tid = target_track.id if target_track else ""
        bus_idx = getattr(track, "synth_output_bus", None) if track else None
        now = time.time()
        sr = max(8000, int(self.sample_rate))
        tail_sec = self._compute_native_tail_sec(native_inst, is_drum)

        with self._live_native_lock:
            live_beat = (self._live_native_sample_pos / float(sr)) * 2.0
            if not is_drum:
                for n in self._live_native_notes:
                    if n["track_id"] == tid and n["pitch"] == int(pitch) and not n["released"]:
                        n["released"] = True
                        n["release_at"] = None
                        n["duration"] = max(0.01, live_beat - n["start_beat"])
                        n["release_beat"] = live_beat
                        n["tail_sec"] = tail_sec

            note_id = self._next_live_note_id
            self._next_live_note_id += 1
            if target_track is not None:
                self._live_native_tracks[tid] = target_track

            self._live_native_notes.append({
                "live_id": note_id,
                "track": target_track,
                "track_id": tid,
                "record_track": track,
                "native_inst": native_inst,
                "bus_idx": bus_idx,
                "is_drum": is_drum,
                "pitch": int(pitch),
                "velocity": max(1, min(127, int(velocity))),
                "press_time": now,
                "press_beat_proj": self.current_beat,
                "start_beat": live_beat,
                "duration": 0.25 if is_drum else 10000.0,
                "released": is_drum,
                "release_at": None,
                "release_beat": live_beat if is_drum else None,
                "tail_sec": tail_sec,
                "env_sample": 0,
                "off_sample": None,
                "_synth_done": False,
            })
            self._live_native_tail_until[tid] = max(
                self._live_native_tail_until.get(tid, 0.0),
                now + max(1.5, tail_sec + 0.5),
            )

    def _flush_scheduled_live_note_offs(self, now: float):
        """Convertit les Note-Off différés arrivés à échéance en messages MIDI immédiats."""
        with self._live_midi_lock:
            if not self._live_scheduled_note_offs:
                return
            still_waiting = []
            for rel_ts, tid, p, channel in self._live_scheduled_note_offs:
                if now >= rel_ts:
                    self._live_midi_queue.setdefault(tid, []).append((bytes([0x80 | channel, p, 0]), 0.0))
                    self._live_tail_until = max(self._live_tail_until, now + 4.0)
                else:
                    still_waiting.append((rel_ts, tid, p, channel))
            self._live_scheduled_note_offs = still_waiting

    def stop_live_note(self, pitch: int, track: Optional[Track] = None, min_gate_sec: Optional[float] = None):
        """
        Relâche une note démarrée avec start_live_note (Note-Off) avec maintien minimal
        optionnel (0.35 s pour les clics souris, 0.0 s pour le clavier d'ordinateur).
        """
        now = time.time()
        gate_sec = 0.35 if min_gate_sec is None else max(0.0, float(min_gate_sec))
        target_track = track
        if track and getattr(track, "synth_route_track_id", None) and self.project:
            routed = self.project.get_track(track.synth_route_track_id)
            if routed:
                target_track = routed

        if target_track:
            note_info = None
            with self._live_midi_lock:
                note_info = self._live_active_notes.pop((target_track.id, int(pitch)), None)
                if note_info is not None:
                    held_sec = max(0.05, now - float(note_info.get("press_time", now)))
                    if gate_sec > 0.0 and held_sec < gate_sec:
                        release_at = float(note_info.get("press_time", now)) + gate_sec
                        self._live_scheduled_note_offs.append((release_at, target_track.id, int(pitch), note_info.get("channel", 0)))
                        self._live_tail_until = max(self._live_tail_until, release_at + 4.0)
                    else:
                        q = self._live_midi_queue.setdefault(target_track.id, [])
                        q.append((bytes([0x80 | note_info.get("channel", 0), int(pitch), 0]), 0.0))
                        self._live_tail_until = max(self._live_tail_until, now + 4.0)
            if note_info is not None:
                if not self._live_direct_active:
                    self._live_vst_event.set()
                rec_track = note_info.get("record_track") or track
                if self.is_recording and rec_track and rec_track.track_type == "midi" and getattr(rec_track, "armed", False):
                    bpm = self.project.bpm if self.project else 120.0
                    held_sec = max(0.25, now - float(note_info.get("press_time", now)))
                    dur_beats = max(0.25, (held_sec * bpm) / 60.0)
                    rel_beat = max(0.0, float(note_info.get("start_beat", self.current_beat)) - self.recording_start_beat)
                    with self._record_lock:
                        self._recorded_midi_notes.append(MidiNote(
                            pitch=int(pitch),
                            start_beat=rel_beat,
                            duration=dur_beats,
                            velocity=int(note_info.get("velocity", 100))
                        ))
                return

        tid = target_track.id if target_track else (track.id if track else "")
        matched_native = None
        sr = max(8000, int(self.sample_rate))
        with self._live_native_lock:
            cur_live_beat = (self._live_native_sample_pos / float(sr)) * 2.0
            for n in reversed(self._live_native_notes):
                if n["track_id"] == tid and n["pitch"] == int(pitch) and not n["released"] and n["release_at"] is None:
                    matched_native = n
                    held_sec = max(0.0, now - float(n["press_time"]))
                    tail_sec = self._compute_native_tail_sec(n["native_inst"], n["is_drum"])
                    n["tail_sec"] = tail_sec
                    if gate_sec > 0.0 and held_sec < gate_sec:
                        release_at = float(n["press_time"]) + gate_sec
                        n["release_at"] = release_at
                        self._live_native_tail_until[tid] = max(
                            self._live_native_tail_until.get(tid, 0.0),
                            release_at + tail_sec + 0.5,
                        )
                    else:
                        n["released"] = True
                        n["duration"] = max(0.01, cur_live_beat - n["start_beat"])
                        n["release_beat"] = cur_live_beat
                        self._live_native_tail_until[tid] = max(
                            self._live_native_tail_until.get(tid, 0.0),
                            now + tail_sec + 0.5,
                        )
                    break

        if matched_native is not None:
            rec_track = matched_native.get("record_track") or track
            if self.is_recording and rec_track and rec_track.track_type == "midi" and getattr(rec_track, "armed", False):
                bpm = self.project.bpm if self.project else 120.0
                held_sec = max(0.25, now - float(matched_native.get("press_time", now)))
                dur_beats = max(0.25, (held_sec * bpm) / 60.0)
                rel_beat = max(0.0, float(matched_native.get("press_beat_proj", self.current_beat)) - self.recording_start_beat)
                with self._record_lock:
                    self._recorded_midi_notes.append(MidiNote(
                        pitch=int(pitch),
                        start_beat=rel_beat,
                        duration=dur_beats,
                        velocity=int(matched_native.get("velocity", 100))
                    ))
            return

        live_key = (track.id if track else "", int(pitch))
        matched_info = None
        with self._preview_lock:
            for item in self._preview_buffers:
                if item.get("live_key") == live_key and not item.get("releasing", False):
                    item["releasing"] = True
                    matched_info = item
                    buf = item["buffer"]
                    cur = item["cursor"]
                    min_gate_frames = int((0.30 if min_gate_sec is None else gate_sec) * self.sample_rate)
                    release_start = max(cur, min(len(buf), min_gate_frames))
                    rem = len(buf) - release_start
                    if rem > 0:
                        inst = item.get("native_inst")
                        rel_param = getattr(inst, "release_sec", None) if inst is not None else None
                        if rel_param is not None:
                            rel_sec = max(0.02, float(rel_param))
                            # Courbe exponentielle : -30 dB à rel_sec, -60 dB à 2.0 * rel_sec
                            fade_len = min(rem, max(1, int(rel_sec * 2.0 * self.sample_rate)))
                            t_arr = np.arange(fade_len, dtype=np.float32) / float(self.sample_rate)
                            env = np.exp(-3.45388 * t_arr / rel_sec).astype(np.float32)
                            tail_ramp = min(fade_len, max(1, int(0.015 * self.sample_rate)))
                            if tail_ramp > 1:
                                env[-tail_ramp:] *= np.linspace(1.0, 0.0, tail_ramp, dtype=np.float32)
                            buf[release_start:release_start + fade_len] *= env[:, None]
                        else:
                            fade_len = min(rem, max(1, int(0.18 * self.sample_rate)))
                            buf[release_start:release_start + fade_len] *= np.linspace(
                                1.0, 0.0, fade_len, dtype=np.float32
                            )[:, None]
                        item["buffer"] = buf[:release_start + fade_len]

        if matched_info is not None:
            rec_track = matched_info.get("record_track") or track
            if self.is_recording and rec_track and rec_track.track_type == "midi" and getattr(rec_track, "armed", False):
                bpm = self.project.bpm if self.project else 120.0
                held_sec = max(0.25, now - float(matched_info.get("press_time", now)))
                dur_beats = max(0.25, (held_sec * bpm) / 60.0)
                rel_beat = max(0.0, float(matched_info.get("start_beat", self.current_beat)) - self.recording_start_beat)
                with self._record_lock:
                    self._recorded_midi_notes.append(MidiNote(
                        pitch=int(pitch),
                        start_beat=rel_beat,
                        duration=dur_beats,
                        velocity=int(matched_info.get("velocity", 100))
                    ))

    @staticmethod
    def _compute_native_tail_sec(native_inst: Any, is_drum: bool) -> float:
        """Retourne la durée de queue (relâchement + effets intégrés) d'un instrument natif."""
        if is_drum:
            return 3.0
        if native_inst is None:
            return 0.20
        if hasattr(native_inst, "get_release_tail_sec"):
            try:
                return max(0.15, float(native_inst.get_release_tail_sec()))
            except Exception:
                pass
        if hasattr(native_inst, "layers"):
            max_rel = max((float(getattr(l, "release", 0.35)) for l in native_inst.layers), default=0.35)
            has_fx = bool(getattr(getattr(native_inst, "reverb", None), "enabled", False) or getattr(getattr(native_inst, "delay", None), "enabled", False))
            return max(0.15, max_rel + (0.60 if has_fx else 0.12))
        return 0.35

    def _render_default_live_synth(self, notes: List[Dict[str, Any]], frames: int, sr: int) -> np.ndarray:
        """Rendu polyphonique bloc-à-bloc sans clic pour le synthétiseur interne par défaut."""
        out_mono = np.zeros(frames, dtype=np.float32)
        if not notes or frames <= 0:
            return np.zeros((frames, 2), dtype=np.float32)

        sr_f = float(max(8000, sr))
        att_s = max(1, int(0.012 * sr_f))
        dec_s = max(1, int(0.060 * sr_f))
        rel_s = max(1, int(0.120 * sr_f))
        sus_lvl = 0.75
        idx = np.arange(frames, dtype=np.float64)

        for n in notes:
            if n.get("_synth_done"):
                continue
            start_s = int(n["env_sample"])
            t = (start_s + idx) / sr_f
            freq = midi_to_freq(int(n["pitch"]))
            sine = np.sin(2.0 * np.pi * freq * t)
            saw = 0.5 * (2.0 * (t * freq - np.floor(0.5 + t * freq)))
            sub = 0.3 * np.sin(2.0 * np.pi * (freq * 0.5) * t)
            sig = 0.6 * sine + 0.3 * saw + 0.2 * sub

            s_pos = start_s + idx
            env = np.empty(frames, dtype=np.float64)
            if not n["released"]:
                env[:] = sus_lvl
                m_att = s_pos < att_s
                if np.any(m_att):
                    env[m_att] = s_pos[m_att] / float(att_s)
                m_dec = (~m_att) & (s_pos < (att_s + dec_s))
                if np.any(m_dec):
                    env[m_dec] = 1.0 - ((s_pos[m_dec] - att_s) / float(dec_s)) * (1.0 - sus_lvl)
            else:
                if n["off_sample"] is None:
                    n["off_sample"] = start_s
                off_s = int(n["off_sample"])
                if off_s < att_s:
                    off_lvl = max(0.0, off_s / float(att_s))
                elif off_s < (att_s + dec_s):
                    off_lvl = 1.0 - ((off_s - att_s) / float(dec_s)) * (1.0 - sus_lvl)
                else:
                    off_lvl = sus_lvl
                rel_pos = np.maximum(0.0, s_pos - off_s)
                env[:] = off_lvl * np.maximum(0.0, 1.0 - (rel_pos / float(rel_s)))
                if (start_s + frames - off_s) >= rel_s:
                    n["_synth_done"] = True

            n["env_sample"] = start_s + frames
            vel_scale = (float(n["velocity"]) / 127.0) * 0.4
            out_mono += (sig * env * vel_scale).astype(np.float32)

        return np.column_stack((out_mono, out_mono))

    def _flush_and_advance_live_native(self, frames: int, now: Optional[float] = None) -> Optional[tuple[float, float, Dict[str, List[Dict[str, Any]]]]]:
        """Met à jour les relâchements différés, purge les voix éteintes et avance l'horloge live native."""
        if not self._live_native_notes and not self._live_native_tail_until:
            return None
        if now is None:
            now = time.time()
        sr = max(8000, int(self.sample_rate))
        with self._live_native_lock:
            if not self._live_native_notes and not self._live_native_tail_until:
                return None
            cur_pos = self._live_native_sample_pos
            cur_live_beat = (cur_pos / float(sr)) * 2.0

            kept: List[Dict[str, Any]] = []
            active_tids = set()
            for n in self._live_native_notes:
                if not n["released"] and n["release_at"] is not None and now >= float(n["release_at"]):
                    n["released"] = True
                    n["release_at"] = None
                    n["duration"] = max(0.01, cur_live_beat - n["start_beat"])
                    n["release_beat"] = cur_live_beat
                    n["tail_sec"] = self._compute_native_tail_sec(n["native_inst"], n["is_drum"])

                if n["released"]:
                    if n["native_inst"] is None and n.get("_synth_done"):
                        continue
                    if n["native_inst"] is not None:
                        end_beat_with_tail = n["start_beat"] + n["duration"] + (n["tail_sec"] * 2.0) + 0.35
                        if cur_live_beat > end_beat_with_tail:
                            continue

                kept.append(n)
                active_tids.add(n["track_id"])

            self._live_native_notes = kept

            # Purger les queues d'effets expirées
            expired_tids = []
            for tid, until in self._live_native_tail_until.items():
                if tid in active_tids or now < until:
                    continue
                trk = self._live_native_tracks.get(tid)
                inst = self.get_native_instrument(trk) if trk else None
                if inst is not None and getattr(inst, "_active_voices", None):
                    continue
                expired_tids.append(tid)
            for tid in expired_tids:
                self._live_native_tail_until.pop(tid, None)
                self._live_native_tracks.pop(tid, None)
                self._live_native_contexts.pop(("novadaw.piano", tid), None)

            if not self._live_native_notes and not self._live_native_tail_until:
                return None

            self._live_native_sample_pos = cur_pos + frames
            end_live_beat = ((cur_pos + frames) / float(sr)) * 2.0

            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for tid in self._live_native_tail_until:
                grouped[tid] = []
            for n in self._live_native_notes:
                grouped.setdefault(n["track_id"], []).append(n)

            return cur_live_beat, end_live_beat, grouped

    def _render_track_live_native_audio(
        self,
        tid: str,
        track: Optional[Track],
        notes: List[Dict[str, Any]],
        start_b: float,
        end_b: float,
        frames: int,
        use_isolated_ctx: bool = False,
    ) -> tuple[np.ndarray, Optional[Any]]:
        """Rend un bloc stéréo de `frames` échantillons pour les notes live d'une piste native."""
        native_inst = None
        bus_idx = None
        for n in notes:
            if n.get("native_inst") is not None:
                native_inst = n["native_inst"]
            if n.get("bus_idx") is not None:
                bus_idx = n["bus_idx"]
            if native_inst is not None:
                break
        if native_inst is None and track is not None:
            native_inst = self.get_native_instrument(track)
        if bus_idx is None and track is not None:
            bus_idx = getattr(track, "synth_output_bus", None)

        if native_inst is None:
            return self._render_default_live_synth(notes, frames, self.sample_rate), None

        note_objs = [
            SimpleNamespace(
                pitch=n["pitch"],
                start_beat=n["start_beat"],
                duration=n["duration"],
                velocity=n["velocity"],
                _live_id=n["live_id"],
            )
            for n in notes
        ]

        ptype = getattr(native_inst, "plugin_type_id", None)
        if ptype == "novadaw.piano":
            # The live keyboard has its own clock, even during sequencer playback.
            ctx_key = (ptype, tid)
            owner, ctx = self._live_native_contexts.get(ctx_key, (None, None))
            if owner is not native_inst:
                ctx = native_inst.create_isolated_context(self.sample_rate)
                self._live_native_contexts[ctx_key] = (native_inst, ctx)
            return native_inst.render_in_context(
                note_objs, start_b, 2.0, frames, self.sample_rate, ctx
            ), native_inst

        if use_isolated_ctx:
            if ptype == "novadaw.guitar" and hasattr(native_inst, "create_isolated_context") and hasattr(native_inst, "_render_slice_internal"):
                ctx_key = ("guitar", tid)
                ctx = self._live_native_contexts.get(ctx_key)
                if ctx is None:
                    ctx = native_inst.create_isolated_context(self.sample_rate)
                    self._live_native_contexts[ctx_key] = ctx
                rev = ctx.get("reverb")
                if rev is not None:
                    rev.enabled = native_inst.reverb_mix > 0.005
                    rev.room_size = max(0.1, min(0.95, float(native_inst.reverb_room)))
                    rev.width = max(0.2, min(1.0, float(native_inst.stereo_width) + 0.35))
                    rev.mix = max(0.0, min(0.65, float(native_inst.reverb_mix)))
                with native_inst._dsp_lock:
                    wave = native_inst._render_slice_internal(
                        notes=note_objs,
                        start_b=start_b,
                        end_b=end_b,
                        beats_per_sec=2.0,
                        frames=frames,
                        sample_rate=self.sample_rate,
                        isolated_ctx=ctx,
                        use_live_state=False,
                    )
                return wave, native_inst

            if ptype == "novadaw.synth" and hasattr(native_inst, "_render_slice_internal"):
                ctx_key = ("synth", tid)
                ctx = self._live_native_contexts.get(ctx_key)
                if ctx is None:
                    from plugins.synth.synth_dsp import StereoPingPongDelay, StereoStudioReverb
                    ctx = {
                        "delay": StereoPingPongDelay(self.sample_rate),
                        "reverb": StereoStudioReverb(self.sample_rate),
                        "svfs": {},
                    }
                    self._live_native_contexts[ctx_key] = ctx
                d, r = ctx["delay"], ctx["reverb"]
                d.enabled = native_inst.delay.enabled
                d.time_sec = native_inst.delay.time_sec
                d.feedback = native_inst.delay.feedback
                d.ping_pong = native_inst.delay.ping_pong
                d.mix = native_inst.delay.mix
                r.enabled = native_inst.reverb.enabled
                r.room_size = native_inst.reverb.room_size
                r.damping = native_inst.reverb.damping
                r.width = native_inst.reverb.width
                r.mix = native_inst.reverb.mix
                with native_inst._dsp_lock:
                    wave = native_inst._render_slice_internal(
                        notes=note_objs,
                        start_b=start_b,
                        end_b=end_b,
                        beats_per_sec=2.0,
                        frames=frames,
                        sample_rate=self.sample_rate,
                        bus_index=bus_idx,
                        target_delay=d,
                        target_reverb=r,
                        voice_svf_dict=ctx["svfs"],
                        use_live_state=False,
                        clean_stale_voices=True,
                    )
                return wave, native_inst

        if hasattr(native_inst, "render_slice"):
            try:
                if bus_idx is not None:
                    try:
                        wave = native_inst.render_slice(
                            note_objs, start_b, end_b, 2.0, frames, self.sample_rate, bus_index=bus_idx
                        )
                    except TypeError:
                        wave = native_inst.render_slice(
                            note_objs, start_b, end_b, 2.0, frames, self.sample_rate
                        )
                else:
                    wave = native_inst.render_slice(
                        note_objs, start_b, end_b, 2.0, frames, self.sample_rate
                    )
                if wave is not None:
                    return wave, native_inst
            except Exception:
                pass

        return np.zeros((frames, 2), dtype=np.float32), native_inst

    def _has_any_solo(self) -> bool:
        """Retourne True si au moins une piste du projet actif est en Solo."""
        proj = self.project
        if proj is None:
            return False
        tracks = getattr(proj, "tracks", None)
        if not tracks:
            return False
        for t in tracks:
            if getattr(t, "soloed", False):
                return True
        return False

    def _peak_abs_1d(self, col: np.ndarray, frames: int) -> float:
        """Calcule max(abs(col[:frames])) sans allocation mémoire dans le callback audio."""
        n = min(frames, len(col))
        if n <= 0:
            return 0.0
        if col.dtype == np.float32:
            scratch = self._cb_mono_scratch
            if scratch is None or len(scratch) < n:
                scratch = np.empty(max(n, 4096), dtype=np.float32)
                self._cb_mono_scratch = scratch
            view = scratch[:n]
            np.abs(col[:n], out=view)
            return float(view.max())
        return float(np.max(np.abs(col[:n])))

    def _record_cb_peak(self, tid: Optional[str], pk_l: float, pk_r: float) -> None:
        """Enregistre le pic L/R par piste dans un slot pré-alloué (zéro tuple/dict par bloc)."""
        if not tid:
            return
        slot = self._cb_peak_slots.get(tid)
        if slot is None:
            self._cb_peak_slots[tid] = [pk_l, pk_r, 1.0]
            self._cb_active_peak_tids.append(tid)
        elif slot[2] == 0.0:
            slot[0] = pk_l
            slot[1] = pk_r
            slot[2] = 1.0
            self._cb_active_peak_tids.append(tid)
        else:
            if pk_l > slot[0]:
                slot[0] = pk_l
            if pk_r > slot[1]:
                slot[1] = pk_r

    @staticmethod
    def _resolve_live_track_gains(track: Optional[Track], has_solo: bool = False) -> Optional[tuple[float, float]]:
        """Retourne (left_gain, right_gain) pour une piste ou None si elle est coupée (Mute/Solo)."""
        if track is None:
            return 1.0, 1.0
        if getattr(track, "muted", False) or (has_solo and not getattr(track, "soloed", False)):
            return None
        vol = max(0.0, float(getattr(track, "volume", 0.8)))
        pan = max(-1.0, min(1.0, float(getattr(track, "pan", 0.0))))
        return vol * (1.0 - max(0.0, pan)), vol * (1.0 + min(0.0, pan))

    @staticmethod
    def _record_block_peak(block_track_peaks: Dict[str, tuple[float, float]], tid: Optional[str], pk_l: float, pk_r: float) -> None:
        if not tid:
            return
        if tid not in block_track_peaks:
            block_track_peaks[tid] = (pk_l, pk_r)
        else:
            old_l, old_r = block_track_peaks[tid]
            block_track_peaks[tid] = (max(old_l, pk_l), max(old_r, pk_r))

    def _render_live_native_direct(
        self,
        live_step: tuple[float, float, Dict[str, List[Dict[str, Any]]]],
        frames: int,
        out: np.ndarray,
        block_track_peaks: Dict[str, tuple[float, float]],
    ) -> None:
        """Rendu temps réel bloc-à-bloc des voix live natives + chaîne d'effets de piste (ampli, EQ, comp...)."""
        start_b, end_b, grouped = live_step
        now = time.time()
        has_solo = self._has_any_solo()
        for tid, notes in grouped.items():
            track = self._live_native_tracks.get(tid)
            if track is None and tid and self.project:
                track = self.project.get_track(tid)

            wave, native_inst = self._render_track_live_native_audio(
                tid, track, notes, start_b, end_b, frames, use_isolated_ctx=False
            )
            if wave is None or len(wave) == 0:
                continue
            if len(wave) != frames:
                buf = np.zeros((frames, 2), dtype=np.float32)
                n_cp = min(frames, len(wave))
                buf[:n_cp] = wave[:n_cp]
                wave = buf

            gains = self._resolve_live_track_gains(track, has_solo)
            if gains is None:
                self._record_block_peak(block_track_peaks, tid, 0.0, 0.0)
                continue

            if track is not None:
                wave = self._process_track_effects_chain(track, wave, start_b, end_b, frames, 120.0, native_inst=native_inst)
                left_gain, right_gain = gains
                wave[:, 0] *= left_gain
                wave[:, 1] *= right_gain

            pk_l = float(np.max(np.abs(wave[:, 0])))
            pk_r = float(np.max(np.abs(wave[:, 1])))
            if max(pk_l, pk_r) > 1e-4:
                with self._live_native_lock:
                    if tid in self._live_native_tail_until:
                        self._live_native_tail_until[tid] = max(self._live_native_tail_until[tid], now + 0.5)

            out += wave
            self._record_block_peak(block_track_peaks, tid, pk_l, pk_r)

    def _render_live_vst_direct(self, frames: int, out: np.ndarray, block_track_peaks: Optional[Dict[str, tuple[float, float]]] = None) -> bool:
        """
        Rendu VST live synchrone bloc-à-bloc sans FIFO intermédiaire (latence minimale).
        Retourne True si le chemin direct SHM a traité le bloc (ou s'il n'y a rien à streamer).
        Retourne False uniquement si un repli sur le thread FIFO (_live_vst_loop) est requis.
        """
        if self.is_playing:
            if self._live_scheduled_note_offs:
                self._flush_scheduled_live_note_offs(time.time())
            self._live_direct_active = False
            return True

        pmgr = self._plugin_mgr
        if pmgr is None:
            from core.plugin_manager import global_plugin_manager
            pmgr = global_plugin_manager
            self._plugin_mgr = pmgr

        if (
            not self._live_scheduled_note_offs
            and not self._live_active_notes
            and not self._live_midi_queue
            and self._live_tail_until == 0.0
            and self._live_last_audible_time == 0.0
            and not pmgr.open_editors
        ):
            self._live_direct_active = False
            return True

        now = time.time()
        if self._live_scheduled_note_offs:
            self._flush_scheduled_live_note_offs(now)

        with self._live_midi_lock:
            has_active_notes = bool(self._live_active_notes) or bool(self._live_scheduled_note_offs)
            has_pending_midi = any(bool(events) for events in self._live_midi_queue.values())
            active_track = self._live_active_track
            tail_active = (now < self._live_tail_until) or (self._live_last_audible_time > 0.0 and (now - self._live_last_audible_time < 1.2))

        open_editor_plugin = None
        open_editor_track: Optional[Track] = None
        if pmgr.open_editors:
            for ed_key, ed_evt in list(pmgr.open_editors.items()):
                if ed_evt.is_set():
                    continue
                inst = pmgr.active_instances.get(ed_key)
                if inst and getattr(inst, "is_instrument", False):
                    open_editor_plugin = inst
                    if self.project:
                        for t in self.project.tracks:
                            if ed_key == f"track:{t.id}:instrument:{t.plugin_path}" or t.plugin_path == ed_key:
                                open_editor_track = t
                                break
                    break

        if not (has_pending_midi or has_active_notes or tail_active or open_editor_plugin is not None):
            self._live_direct_active = False
            return True

        target_track = active_track or open_editor_track
        vst_plugin = None
        if target_track is not None:
            vst_plugin = self.get_track_plugin(target_track)
        if vst_plugin is None and open_editor_plugin is not None:
            vst_plugin = open_editor_plugin

        if not self._can_render_vst_offline(vst_plugin):
            self._live_direct_active = False
            return True

        # Utiliser le pont mémoire partagée synchrone si disponible (0 bloc de FIFO supplémentaire)
        if not (hasattr(vst_plugin, "render_live_block") and getattr(vst_plugin, "_shm", None) is not None):
            self._live_direct_active = False
            self._live_vst_event.set()
            return False

        self._live_direct_active = True
        midi_msgs: List[tuple[bytes, float]] = []
        with self._live_midi_lock:
            if target_track and target_track.id in self._live_midi_queue:
                midi_msgs = list(self._live_midi_queue.pop(target_track.id, []))
            elif self._live_midi_queue:
                for tid in list(self._live_midi_queue.keys()):
                    midi_msgs.extend(self._live_midi_queue.pop(tid, []))

        if self._live_vst_buf is None or self._live_vst_buf.shape != (frames, 2):
            self._live_vst_buf = np.zeros((frames, 2), dtype=np.float32)

        wave = self._live_vst_buf
        if not vst_plugin.render_live_block(midi_msgs, frames, self.sample_rate, wave):
            return True

        peak = float(np.max(np.abs(wave)))
        if peak > 1e-4:
            self._live_last_audible_time = now
            with self._live_midi_lock:
                if self._live_tail_until > 0.0:
                    self._live_tail_until = max(self._live_tail_until, now + 1.2)

        if target_track is not None:
            gains = self._resolve_live_track_gains(target_track, self._has_any_solo())
            if gains is None:
                if block_track_peaks is not None:
                    self._record_block_peak(block_track_peaks, target_track.id, 0.0, 0.0)
                else:
                    self._record_cb_peak(target_track.id, 0.0, 0.0)
                return True
            processed = self._process_track_effects_isolated(target_track, wave, None)
            left_gain, right_gain = gains
            processed[:, 0] *= left_gain
            processed[:, 1] *= right_gain
            out += processed
            if len(processed) > 0:
                pk_l = self._peak_abs_1d(processed[:, 0], len(processed))
                pk_r = self._peak_abs_1d(processed[:, 1], len(processed))
                if block_track_peaks is not None:
                    self._record_block_peak(block_track_peaks, target_track.id, pk_l, pk_r)
                else:
                    self._record_cb_peak(target_track.id, pk_l, pk_r)
        else:
            out += wave
        return True

    def _live_vst_loop(self):
        """
        Thread de streaming temps réel de secours (pour bridges sans mémoire partagée comme VST2 32-bit).
        Lorsque le pont mémoire partagée est actif, _render_live_vst_direct gère le rendu synchrone sans FIFO.
        """
        from core.plugin_manager import global_plugin_manager
        last_audible_time = 0.0

        while not self._cache_closed:
            if self._live_direct_active or self.is_playing:
                with self._live_stream_lock:
                    if self._live_stream_chunks:
                        self._live_stream_chunks.clear()
                self._live_vst_event.wait(timeout=0.025)
                self._live_vst_event.clear()
                continue

            now = time.time()
            self._flush_scheduled_live_note_offs(now)

            # Identifier les cibles VST actives (notes jouées, queue de release, ou éditeur VST ouvert)
            active_track: Optional[Track] = None
            has_pending_midi = False
            has_note_on = False
            with self._live_midi_lock:
                has_active_notes = bool(self._live_active_notes) or bool(self._live_scheduled_note_offs)
                for tid, events in self._live_midi_queue.items():
                    if events:
                        has_pending_midi = True
                        if any((ev[0][0] & 0xF0) == 0x90 and ev[0][2] > 0 for ev in events):
                            has_note_on = True
                active_track = self._live_active_track
                tail_active = (now < self._live_tail_until) or (now - last_audible_time < 1.2)

            open_editor_plugin = None
            open_editor_track: Optional[Track] = None
            if global_plugin_manager.open_editors:
                for ed_key, ed_evt in list(global_plugin_manager.open_editors.items()):
                    if ed_evt.is_set():
                        continue
                    inst = global_plugin_manager.active_instances.get(ed_key)
                    if inst and getattr(inst, "is_instrument", False):
                        open_editor_plugin = inst
                        if self.project:
                            for t in self.project.tracks:
                                if ed_key == f"track:{t.id}:instrument:{t.plugin_path}" or t.plugin_path == ed_key:
                                    open_editor_track = t
                                    break
                        break

            should_stream = has_pending_midi or has_active_notes or tail_active or (open_editor_plugin is not None)
            if not should_stream:
                self._live_vst_event.wait(timeout=0.020)
                self._live_vst_event.clear()
                continue

            target_track = active_track or open_editor_track
            vst_plugin = None
            if target_track is not None:
                vst_plugin = self.get_track_plugin(target_track)
            if vst_plugin is None and open_editor_plugin is not None:
                vst_plugin = open_editor_plugin

            if not self._can_render_vst_offline(vst_plugin):
                self._live_vst_event.wait(timeout=0.015)
                self._live_vst_event.clear()
                continue

            # Si le plugin supporte le mode synchrone mémoire partagée, laisser _audio_callback le gérer directement
            if hasattr(vst_plugin, "render_live_block") and getattr(vst_plugin, "_shm", None) is not None:
                self._live_vst_event.wait(timeout=0.020)
                self._live_vst_event.clear()
                continue

            # File d'attente minimale (1 seul bloc) pour le fallback 32-bit
            chunk_frames = max(128, int(self.block_size))
            target_buffered_frames = chunk_frames
            with self._live_stream_lock:
                if has_note_on and self._live_stream_chunks:
                    kept = []
                    for c in self._live_stream_chunks:
                        rem_buf = c["buffer"][c["cursor"]:]
                        if len(rem_buf) > 0 and float(np.max(np.abs(rem_buf))) > 1e-4:
                            kept.append(c)
                    self._live_stream_chunks = kept
                queued_frames = sum(len(c["buffer"]) - c["cursor"] for c in self._live_stream_chunks)

            if queued_frames >= target_buffered_frames:
                self._live_vst_event.wait(timeout=0.004)
                self._live_vst_event.clear()
                continue

            midi_msgs: List[tuple[bytes, float]] = []
            with self._live_midi_lock:
                if target_track and target_track.id in self._live_midi_queue:
                    midi_msgs = list(self._live_midi_queue.pop(target_track.id, []))
                elif self._live_midi_queue:
                    for tid in list(self._live_midi_queue.keys()):
                        midi_msgs.extend(self._live_midi_queue.pop(tid, []))

            dur_sec = chunk_frames / float(self.sample_rate)
            try:
                vst_out = vst_plugin(
                    midi_msgs,
                    duration=dur_sec,
                    sample_rate=self.sample_rate,
                    num_channels=2,
                    reset=False,
                )
                wave = None
                if vst_out is not None and vst_out.size > 0:
                    if vst_out.ndim == 1:
                        wave = np.column_stack((vst_out, vst_out))
                    elif vst_out.shape[0] == 1:
                        wave = np.column_stack((vst_out[0], vst_out[0]))
                    else:
                        wave = vst_out[:2].T

                if wave is not None and len(wave) > 0:
                    peak = float(np.max(np.abs(wave)))
                    if peak > 1e-4:
                        last_audible_time = time.time()
                        with self._live_midi_lock:
                            if self._live_tail_until > 0.0:
                                self._live_tail_until = max(self._live_tail_until, last_audible_time + 1.0)

                    if target_track is not None:
                        wave = self._process_track_effects_isolated(target_track, wave, None)

                    with self._live_stream_lock:
                        self._live_stream_chunks.append({
                            "buffer": np.ascontiguousarray(wave, dtype=np.float32),
                            "cursor": 0,
                            "track": target_track,
                            "track_id": target_track.id if target_track else None,
                        })
            except Exception:
                self._live_vst_event.wait(timeout=0.015)

    def preview_note(self, pitch: int, duration_sec: float = 0.35, velocity: int = 100, track: Optional[Track] = None):
        """Joue immédiatement une note (appelé par le Piano Roll ou les tests)"""
        stream_active = bool(
            self._stream is not None
            and getattr(self._stream, "active", False)
            and os.environ.get("NOVADAW_SILENT_TESTS") != "1"
        )
        if stream_active and track and track.plugin_path and not str(track.plugin_path).startswith("novadaw."):
            self.start_live_note(pitch, velocity=velocity, track=track)
            self.stop_live_note(pitch, track=track)
            return

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
                    on_msg = (bytes([self._midi_status(0x90, track), pitch, max(1, min(127, velocity))]), 0.0)
                    off_msg = (bytes([self._midi_status(0x80, track), pitch, 0]), duration_sec * 0.8)
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

        # Application de la chaîne d'effets de la piste (Égaliseur, Compresseur, VST) ; volume/pan appliqués en direct dans _audio_callback
        if wave is not None and len(wave) > 0 and track:
            wave = self._process_track_effects_isolated(track, wave.copy(), native_inst, reset_clones=True)

        with self._preview_lock:
            self._preview_buffers.append({
                "buffer": np.ascontiguousarray(wave, dtype=np.float32),
                "cursor": 0,
                "track": track,
                "record_track": track,
                "track_id": track.id if track else None,
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

    @staticmethod
    def _is_non_insert_plugin(plugin: Any, track: Track, native_inst: Optional[Any] = None) -> bool:
        """Retourne True si le plugin est un instrument générateur ou un mixeur Master à exclure des inserts de piste."""
        if plugin is native_inst:
            return True
        if getattr(plugin, "is_instrument", False) or getattr(plugin, "category", "") == "instrument":
            return True
        if getattr(plugin, "plugin_type_id", None) == "novadaw.mixer" and getattr(track, "track_type", "") != "master":
            return True
        return False

    @staticmethod
    def _normalize_plugin_output(res: Any, fallback: np.ndarray, expected_frames: int) -> np.ndarray:
        """Garantit qu'un plugin d'effet renvoie toujours un buffer stéréo float32 (expected_frames, 2) valide sans allocation inutile."""
        if res is None:
            return fallback
        if isinstance(res, np.ndarray) and res.dtype == np.float32 and res.ndim == 2 and res.shape == (expected_frames, 2):
            arr = res
        else:
            arr = np.asarray(res, dtype=np.float32)
            if arr.size == 0:
                return fallback
            if arr.ndim == 1:
                arr = np.column_stack((arr, arr))
            elif arr.shape[1] == 1:
                arr = np.column_stack((arr[:, 0], arr[:, 0]))
            elif arr.shape[1] > 2:
                arr = arr[:, :2]
            if len(arr) != expected_frames:
                out = np.zeros((expected_frames, 2), dtype=np.float32)
                n = min(expected_frames, len(arr))
                out[:n] = arr[:n]
                arr = out
        if expected_frames > 0 and not math.isfinite(float(np.sum(arr, dtype=np.float64))):
            arr = np.nan_to_num(arr, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
        return arr

    @staticmethod
    def _clone_native_plugin(plugin: Any) -> Optional[Any]:
        """Crée un clone isolé d'un plugin natif NovaDAW (avec son propre contexte C natif privé)."""
        ptype = getattr(plugin, "plugin_type_id", None)
        if not ptype or not str(ptype).startswith("novadaw."):
            return None
        try:
            from plugins.registry import plugin_registry
            clone = plugin_registry.create_plugin(ptype)
            if clone is None:
                return None
            if hasattr(plugin, "get_state") and hasattr(clone, "set_state"):
                clone.set_state(copy.deepcopy(plugin.get_state()))
            clone.enabled = bool(getattr(plugin, "enabled", True))
            if hasattr(plugin, "preset_name"):
                try:
                    clone.preset_name = plugin.preset_name
                except Exception:
                    pass
            return clone
        except Exception:
            return None

    def _process_track_effects_isolated(
        self,
        track: Track,
        audio: np.ndarray,
        native_inst: Optional[Any] = None,
        reset_clones: bool = False,
    ) -> np.ndarray:
        """
        Traite le buffer audio d'une note ou prévisualisation à travers tous les effets de la piste
        (plugins natifs avec états C isolés, ainsi qu'effets VST3) sans perturber les mémoires
        continues de la lecture du projet.
        """
        if track is None or audio is None or len(audio) == 0:
            return audio

        has_inserts = bool(getattr(track, "insert_effects", None)) and any(track.insert_effects)
        has_native_fx = False
        if getattr(track, "plugins", None):
            for p in track.plugins:
                if self._is_non_insert_plugin(p, track, native_inst):
                    continue
                if getattr(p, "enabled", True):
                    has_native_fx = True
                    break

        if not has_inserts and not has_native_fx:
            if audio.ndim == 1:
                return np.column_stack((audio, audio)).astype(np.float32)
            return audio if audio.dtype == np.float32 else audio.astype(np.float32)

        if audio.ndim == 1:
            out_audio = np.column_stack((audio, audio)).astype(np.float32)
        else:
            out_audio = audio.copy().astype(np.float32)
        expected_frames = len(out_audio)

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

        # 2. Plugins natifs empilés via clones C isolés (zéro mutation de _native / _c_state live)
        if hasattr(track, "plugins") and track.plugins:
            cur_sgen = self._state_generation
            tid = getattr(track, "id", "") or ""
            for plugin in track.plugins:
                if self._is_non_insert_plugin(plugin, track, native_inst):
                    continue
                if not getattr(plugin, "enabled", True):
                    continue

                key = (id(plugin), tid)
                cached_entry = self._isolated_fx_clones.get(key)
                clone = None
                if cached_entry is not None:
                    cached_gen, clone = cached_entry
                    if cached_gen != cur_sgen and clone is not None:
                        try:
                            if hasattr(plugin, "get_state") and hasattr(clone, "set_state"):
                                clone.set_state(copy.deepcopy(plugin.get_state()))
                            clone.enabled = bool(getattr(plugin, "enabled", True))
                            if hasattr(plugin, "preset_name"):
                                clone.preset_name = plugin.preset_name
                            self._isolated_fx_clones[key] = (cur_sgen, clone)
                        except Exception:
                            clone = None
                if clone is None:
                    clone = self._clone_native_plugin(plugin)
                    if clone is not None:
                        self._isolated_fx_clones[key] = (cur_sgen, clone)

                target_proc = clone if clone is not None else plugin
                if clone is not None and reset_clones and hasattr(clone, "reset"):
                    try:
                        clone.reset()
                    except Exception:
                        pass

                try:
                    res = target_proc.process(out_audio, self.sample_rate)
                    out_audio = self._normalize_plugin_output(res, out_audio, expected_frames)
                except Exception:
                    pass

        return out_audio

    def get_track_for_plugin(self, plugin: Any) -> Optional[Track]:
        """Retrouve la piste qui héberge le plugin donné (instance exacte d'abord, puis fallback)."""
        if not self.project or plugin is None:
            return None
        all_tracks = list(getattr(self.project, "tracks", []) or [])
        if getattr(self.project, "master_track", None):
            all_tracks.append(self.project.master_track)

        # Passe 1 : correspondance exacte par référence d'objet
        for t in all_tracks:
            if hasattr(t, "plugins") and any(p is plugin for p in t.plugins):
                return t

        # Passe 2 : correspondance par instance_id
        target_inst_id = getattr(plugin, "instance_id", None)
        if target_inst_id:
            for t in all_tracks:
                if hasattr(t, "plugins") and any(getattr(p, "instance_id", None) == target_inst_id for p in t.plugins):
                    return t

        # Passe 3 : fallback par plugin_path sur les pistes (si ouvert depuis le Rack avant instanciation)
        target_type_id = getattr(plugin, "plugin_type_id", None)
        if target_type_id:
            for t in getattr(self.project, "tracks", []) or []:
                if getattr(t, "plugin_path", None) == target_type_id:
                    return t
        return None

    def play_preview_buffer(self, wave: np.ndarray, choke_group: int = 0, track: Optional[Track] = None) -> bool:
        """
        Joue immédiatement un buffer audio stéréo en direct dans le flux en temps réel (latence < 10ms).
        Prend en charge les choke groups et applique la chaîne d'inserts de la piste si spécifiée.
        Le volume, le panoramique et Mute/Solo de la piste sont appliqués en temps réel dans _audio_callback.
        """
        if wave is None or len(wave) == 0:
            return False
        if not (self._stream and getattr(self._stream, "active", False)):
            return False

        if wave.ndim == 1:
            wave = np.column_stack((wave, wave)).astype(np.float32)
        else:
            wave = np.ascontiguousarray(wave[:, :2], dtype=np.float32)

        if track is not None:
            native_inst = self.get_native_instrument(track)
            wave = self._process_track_effects_isolated(track, wave.copy(), native_inst, reset_clones=True)

        target_tid = track.id if track else None
        with self._preview_lock:
            if choke_group > 0:
                # Choke group : étouffer immédiatement les voix actives du même groupe sur la piste
                for item in self._preview_buffers:
                    if item.get("choke_group") == choke_group and (
                        target_tid is None or item.get("track_id") in (None, target_tid)
                    ):
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
                "choke_group": choke_group,
                "track": track,
                "record_track": track,
                "track_id": target_tid,
            })
        return True

    def _audio_callback(self, outdata, frames, time_info, status):
        # Buffer de sortie pré-alloué (zéro allocation temps réel)
        if self._cb_out_buf is None or self._cb_out_buf.shape != (frames, 2):
            self._cb_out_buf = np.zeros((frames, 2), dtype=np.float32)
        else:
            self._cb_out_buf.fill(0.0)
        out = self._cb_out_buf
        out_dirty = False

        # Réinitialiser les slots pré-alloués de crête par piste (zéro dict/tuple par bloc)
        if self._cb_active_peak_tids:
            for prev_tid in self._cb_active_peak_tids:
                slot = self._cb_peak_slots.get(prev_tid)
                if slot is not None:
                    slot[0] = 0.0
                    slot[1] = 0.0
                    slot[2] = 0.0
            self._cb_active_peak_tids.clear()

        has_solo_global = self._has_any_solo()
        dt_sec = frames / float(max(8000, int(self.sample_rate)))

        # 1. Mixer les sons de prévisualisation (uniquement si la file n'est pas vide)
        if self._preview_buffers:
            with self._preview_lock:
                if self._preview_buffers:
                    active_previews = []
                    for item in self._preview_buffers:
                        buf = item["buffer"]
                        cur = item["cursor"]
                        avail = len(buf) - cur
                        to_copy = min(frames, avail)
                        if to_copy > 0:
                            slice_data = buf[cur:cur + to_copy]
                            item["cursor"] += to_copy

                            item_track = item.get("track") or item.get("record_track")
                            tid = getattr(item_track, "id", None) if item_track else item.get("track_id")
                            gains = self._resolve_live_track_gains(item_track, has_solo_global)
                            if gains is None:
                                self._record_cb_peak(tid, 0.0, 0.0)
                            else:
                                g_l, g_r = gains
                                out[:to_copy, 0] += slice_data[:, 0] * g_l
                                out[:to_copy, 1] += slice_data[:, 1] * g_r
                                out_dirty = True
                                if tid and to_copy > 0:
                                    pk_l = self._peak_abs_1d(slice_data[:, 0], to_copy) * g_l
                                    pk_r = self._peak_abs_1d(slice_data[:, 1], to_copy) * g_r
                                    self._record_cb_peak(tid, pk_l, pk_r)

                        if item["cursor"] < len(buf):
                            active_previews.append(item)
                    self._preview_buffers = active_previews

        # 1.bis Rendu live natif bloc-à-bloc (court-circuité sans verrou ni time.time() au repos)
        live_native_step = (
            self._flush_and_advance_live_native(frames)
            if (self._live_native_notes or self._live_native_tail_until)
            else None
        )
        if not self.is_playing and live_native_step is not None:
            legacy_peaks: Dict[str, tuple[float, float]] = {}
            self._render_live_native_direct(live_native_step, frames, out, legacy_peaks)
            out_dirty = True
            for l_tid, (l_pl, l_pr) in legacy_peaks.items():
                self._record_cb_peak(l_tid, l_pl, l_pr)

        # 1.ter Rendu VST live synchrone (mémoire partagée, 0 bloc de FIFO) ou fallback FIFO
        handled_direct = self._render_live_vst_direct(frames, out, None)
        if self._live_direct_active:
            out_dirty = True
        if not handled_direct or self._live_stream_chunks:
            need_wake = False
            with self._live_stream_lock:
                written = 0
                while written < frames and self._live_stream_chunks:
                    head = self._live_stream_chunks[0]
                    buf = head["buffer"]
                    cur = head["cursor"]
                    avail = len(buf) - cur
                    take = min(frames - written, avail)
                    if take > 0:
                        slice_data = buf[cur:cur + take]
                        head_track = head.get("track")
                        tid = getattr(head_track, "id", None) if head_track else head.get("track_id")
                        gains = self._resolve_live_track_gains(head_track, has_solo_global)
                        if gains is None:
                            self._record_cb_peak(tid, 0.0, 0.0)
                        else:
                            g_l, g_r = gains
                            out[written:written + take, 0] += slice_data[:, 0] * g_l
                            out[written:written + take, 1] += slice_data[:, 1] * g_r
                            out_dirty = True
                            if tid and take > 0:
                                pk_l = self._peak_abs_1d(slice_data[:, 0], take) * g_l
                                pk_r = self._peak_abs_1d(slice_data[:, 1], take) * g_r
                                self._record_cb_peak(tid, pk_l, pk_r)
                        head["cursor"] = cur + take
                        written += take

                    if head["cursor"] >= len(buf):
                        self._live_stream_chunks.pop(0)
                        need_wake = True
            if need_wake and not handled_direct:
                self._live_vst_event.set()

        # 2. Si le transport est en lecture, mixer les pistes du projet (et le monitoring live éventuel)
        live_in_buf = None
        if self._monitor_enabled and self.project:
            live_in_buf = self._consume_monitor_block(frames)

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

            has_solo = has_solo_global

            # Rendu piste par piste avec buffers scratch pré-alloués et auto-bypass silence
            self._playback_live_step = live_native_step
            try:
                for track in self.project.tracks:
                    tid = track.id
                    if track.muted or (has_solo and not track.soloed):
                        idle_st = self._track_idle_state.get(tid)
                        if idle_st is not None and idle_st.get("active", False):
                            idle_st["active"] = False
                            idle_st["silent_frames"] = 0
                            self._reset_track_insert_fx(track)
                        self._record_cb_peak(tid, 0.0, 0.0)
                        continue

                    track_live = (
                        live_in_buf
                        if (live_in_buf is not None and track.track_type == "audio" and getattr(track, "monitoring", False))
                        else None
                    )
                    tbuf = self._cb_track_bufs.get(tid)
                    if tbuf is None or tbuf.shape != (frames, 2):
                        tbuf = np.zeros((frames, 2), dtype=np.float32)
                        self._cb_track_bufs[tid] = tbuf

                    track_signal = self._render_track_slice(
                        track, start_beat, end_beat, frames, bpm,
                        live_input_buf=track_live, update_mixer_peaks=False,
                        out_buf=tbuf, verify_inst_sig=False, allow_none_on_silence=True,
                    )
                    if track_signal is None:
                        if getattr(track, "automation_lanes", None):
                            _, _, eff_vol, eff_pan = self._compute_track_automation_gains(
                                track, start_beat, end_beat, frames
                            )
                        else:
                            eff_vol = float(getattr(track, "volume", 1.0))
                            eff_pan = max(-1.0, min(1.0, float(getattr(track, "pan", 0.0))))
                        vals = self._live_automation_values.get(tid)
                        if vals is None:
                            self._live_automation_values[tid] = {"volume": eff_vol, "pan": eff_pan}
                        else:
                            vals["volume"] = eff_vol
                            vals["pan"] = eff_pan
                        self._record_cb_peak(tid, 0.0, 0.0)
                        continue

                    left_gain, right_gain, eff_vol, eff_pan = self._compute_track_automation_gains(
                        track, start_beat, end_beat, frames
                    )
                    track_signal[:, 0] *= left_gain
                    track_signal[:, 1] *= right_gain
                    vals = self._live_automation_values.get(tid)
                    if vals is None:
                        self._live_automation_values[tid] = {"volume": eff_vol, "pan": eff_pan}
                    else:
                        vals["volume"] = eff_vol
                        vals["pan"] = eff_pan
                    out += track_signal
                    out_dirty = True

                    if frames > 0:
                        pk_l = self._peak_abs_1d(track_signal[:, 0], frames)
                        pk_r = self._peak_abs_1d(track_signal[:, 1], frames)
                        self._record_cb_peak(tid, pk_l, pk_r)
            finally:
                self._playback_live_step = None

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
        elif self._monitor_enabled and self.project and live_in_buf is not None:
            # Mode Monitoring Live à l'arrêt : permet de chanter et voir/entendre les plugins en direct
            bpm = max(20.0, self.project.bpm)
            has_solo = has_solo_global
            for track in self.project.tracks:
                if track.track_type != "audio" or not getattr(track, "monitoring", False):
                    continue
                if track.muted or (has_solo and not track.soloed):
                    self._record_cb_peak(track.id, 0.0, 0.0)
                    continue
                mon_sig = self._process_track_effects_chain(track, live_in_buf.copy(), 0.0, 0.0, frames, bpm)
                if mon_sig is not None and len(mon_sig) > 0:
                    pan = max(-1.0, min(1.0, float(track.pan)))
                    g_l = float(track.volume) * (1.0 - max(0.0, pan))
                    g_r = float(track.volume) * (1.0 + min(0.0, pan))
                    mon_sig[:, 0] *= g_l
                    mon_sig[:, 1] *= g_r
                    out += mon_sig
                    out_dirty = True
                    pk_l = self._peak_abs_1d(mon_sig[:, 0], len(mon_sig))
                    pk_r = self._peak_abs_1d(mon_sig[:, 1], len(mon_sig))
                    self._record_cb_peak(track.id, pk_l, pk_r)

        # Mise à jour des niveaux de crête des pistes dans le mixeur (audio live + lecture)
        mixer_plugin = self._get_active_mixer_plugin()
        if mixer_plugin and self._cb_active_peak_tids:
            for tid in self._cb_active_peak_tids:
                slot = self._cb_peak_slots[tid]
                mixer_plugin.update_track_peak(tid, slot[0], slot[1], dt_sec=dt_sec)

        # 3. Traitement de la piste Master et de sa pile de plugins (Mixeur, EQ, Compresseur)
        has_active_mixer = False
        master_t = getattr(self.project, "master_track", None) if self.project else None
        if master_t is not None:
            master_plugins = getattr(master_t, "plugins", None) or []
            for mp in master_plugins:
                if getattr(mp, "plugin_type_id", "") == "novadaw.mixer" and getattr(mp, "enabled", True):
                    has_active_mixer = True
                    break
            if master_t.muted:
                out.fill(0.0)
                out_dirty = False
            else:
                has_master_vst = bool(getattr(master_t, "insert_effects", None)) and any(master_t.insert_effects)
                if not out_dirty and not has_master_vst and all(
                    str(getattr(mp, "plugin_type_id", "") or "").startswith("novadaw.")
                    for mp in master_plugins
                    if getattr(mp, "enabled", True)
                ):
                    if has_active_mixer and len(master_plugins) == 1:
                        try:
                            master_plugins[0].process(out, self.sample_rate)
                        except Exception:
                            pass
                else:
                    out_dirty = True
                    if not has_active_mixer:
                        vol = master_t.volume
                        pan = max(-1.0, min(1.0, master_t.pan))
                        out[:, 0] *= vol * (1.0 - max(0.0, pan))
                        out[:, 1] *= vol * (1.0 + min(0.0, pan))
                    if has_master_vst:
                        out = self._process_vst_effects(master_t, out)
                    if master_plugins:
                        for plugin in master_plugins:
                            if getattr(plugin, "enabled", True):
                                try:
                                    res = plugin.process(out, self.sample_rate)
                                    out = self._normalize_plugin_output(res, out, frames)
                                except Exception:
                                    pass

        if not out_dirty:
            if self._master_peak_l > 0.0 or self._master_peak_r > 0.0 or self._master_clipped:
                with self._master_peak_lock:
                    self._master_peak_l *= 0.82
                    self._master_peak_r *= 0.82
                    if self._master_peak_l < 1e-6:
                        self._master_peak_l = 0.0
                    if self._master_peak_r < 1e-6:
                        self._master_peak_r = 0.0
                    if self._master_clipped and (time.time() - self._master_clip_time > 1.8):
                        self._master_clipped = False
            outdata.fill(0.0)
            return

        # Application du volume Master si non déjà appliqué par le plugin Mixeur
        if not has_active_mixer:
            out *= self.master_volume

        # Mesure des niveaux de crête et soft-limiter Master (zéro allocation + fast-path < 0.85)
        if frames > 0:
            if not math.isfinite(float(np.sum(out, dtype=np.float64))):
                np.nan_to_num(out, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
            pk_l = self._peak_abs_1d(out[:, 0], frames)
            pk_r = self._peak_abs_1d(out[:, 1], frames)
            is_clip = bool(pk_l > 1.0 or pk_r > 1.0)
            with self._master_peak_lock:
                self._master_peak_l = max(self._master_peak_l * 0.82, pk_l)
                self._master_peak_r = max(self._master_peak_r * 0.82, pk_r)
                if is_clip:
                    self._master_clipped = True
                    self._master_clip_time = time.time()
                elif self._master_clipped and (time.time() - self._master_clip_time > 1.8):
                    self._master_clipped = False

            max_pk = pk_l if pk_l >= pk_r else pk_r
            if max_pk == 0.0:
                outdata.fill(0.0)
                return
            if max_pk >= 0.85:
                # Genou doux C1 continu au-dessus de 0.85 borné dans [-1.0, 1.0] sans discontinuité inter-blocs
                mask = np.abs(out) > 0.85
                if np.any(mask):
                    over = out[mask]
                    out[mask] = np.sign(over) * (0.85 + 0.15 * np.tanh((np.abs(over) - 0.85) / 0.15))

        np.copyto(outdata, out)

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
                        # L'hôte VST attend un buffer planaire (channels, frames)
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
            self._cache_generation += 1
            self._state_generation += 1
            if track_id:
                self._track_generations[track_id] = self._track_generations.get(track_id, 0) + 1
                self._track_fx_generations[track_id] = self._track_fx_generations.get(track_id, 0) + 1
                self._track_audio_caches.pop(track_id, None)
                self._track_dry_caches.pop(track_id, None)
                self._track_idle_state.pop(track_id, None)
                # MIDI tracks routed to this instrument share its sound settings.
                for track in self.project.tracks if self.project else []:
                    if getattr(track, "synth_route_track_id", None) == track_id:
                        rtid = track.id
                        self._track_generations[rtid] = self._track_generations.get(rtid, 0) + 1
                        self._track_fx_generations[rtid] = self._track_fx_generations.get(rtid, 0) + 1
                        self._track_audio_caches.pop(rtid, None)
                        self._track_dry_caches.pop(rtid, None)
                        self._track_idle_state.pop(rtid, None)
            else:
                all_ids = set(self._track_generations.keys()) | set(self._track_fx_generations.keys())
                if self.project:
                    all_ids.update(t.id for t in self.project.tracks)
                for tid in all_ids:
                    self._track_generations[tid] = self._track_generations.get(tid, 0) + 1
                    self._track_fx_generations[tid] = self._track_fx_generations.get(tid, 0) + 1
                self._track_audio_caches.clear()
                self._track_dry_caches.clear()
                self._track_idle_state.clear()
            self._cache_done_cond.notify_all()
        if trigger_async:
            self._schedule_debounce_caching()

    def refresh_modified_tracks_cache(self, trigger_async: bool = True):
        """Invalide chirurgicalement uniquement les pistes dont le contenu, l'instrument ou les effets ont changé."""
        if not self.project:
            return
        bpm = max(20.0, self.project.bpm)
        sr = self.sample_rate
        valid_ids = {t.id for t in self.project.tracks}
        changed = False
        with self._track_cache_lock:
            for cached_id in list(self._track_audio_caches.keys()):
                if cached_id not in valid_ids:
                    self._track_audio_caches.pop(cached_id, None)
                    self._track_dry_caches.pop(cached_id, None)
                    self._track_idle_state.pop(cached_id, None)
                    changed = True
            for track in self.project.tracks:
                tid = track.id
                gen_hash = self._compute_track_generator_hash(track, bpm, sr)
                thash = self._compute_track_hash(track, bpm, sr, gen_hash=gen_hash)
                dry_cache = self._track_dry_caches.get(tid)
                if dry_cache is not None and (
                    dry_cache.get("gen_hash") != gen_hash
                    or dry_cache.get("bpm") != bpm
                    or dry_cache.get("sample_rate") != sr
                ):
                    self._track_dry_caches.pop(tid, None)
                    self._track_generations[tid] = self._track_generations.get(tid, 0) + 1
                cache = self._track_audio_caches.get(tid)
                if cache is None:
                    changed = True
                    continue
                if cache.get("version_hash") != thash or cache.get("bpm") != bpm or cache.get("sample_rate") != sr:
                    self._track_audio_caches.pop(tid, None)
                    self._track_fx_generations[tid] = self._track_fx_generations.get(tid, 0) + 1
                    self._track_idle_state.pop(tid, None)
                    changed = True
            if changed:
                self._cache_generation += 1
                self._state_generation += 1
                self._cache_done_cond.notify_all()
        if trigger_async and changed:
            self._schedule_debounce_caching()

    def notify_plugin_modified(self, plugin: Any):
        """Notifie le moteur qu'un instrument ou effet a été modifié en direct (bascule temps réel immédiate < 1 ms)."""
        if not self.project or plugin is None:
            return
        plugin_inst_id = getattr(plugin, "instance_id", None)
        inst_track_ids: List[str] = []
        fx_track_ids: List[str] = []
        on_master = False

        if getattr(self.project, "master_track", None):
            for p in getattr(self.project.master_track, "plugins", []):
                if p is plugin or (plugin_inst_id and getattr(p, "instance_id", None) == plugin_inst_id):
                    on_master = True
                    break

        for track in self.project.tracks:
            native_inst = self.get_native_instrument(track)
            for p in getattr(track, "plugins", []):
                if p is plugin or (plugin_inst_id and getattr(p, "instance_id", None) == plugin_inst_id):
                    if self._is_non_insert_plugin(p, track, native_inst):
                        inst_track_ids.append(track.id)
                    else:
                        fx_track_ids.append(track.id)
                    break

        if on_master and not inst_track_ids and not fx_track_ids:
            with self._track_cache_lock:
                self._state_generation += 1
            return

        for tid in inst_track_ids:
            self.invalidate_track_cache(tid, trigger_async=True)

        if fx_track_ids:
            with self._track_cache_lock:
                self._cache_generation += 1
                self._state_generation += 1
                for tid in fx_track_ids:
                    self._track_fx_generations[tid] = self._track_fx_generations.get(tid, 0) + 1
                    self._track_audio_caches.pop(tid, None)
                    self._track_idle_state.pop(tid, None)
                self._cache_done_cond.notify_all()
            self._schedule_debounce_caching()

    def _fast_inst_signature(self, track: Track) -> Any:
        """Empreinte ultra-légère (~1 µs) de l'état de l'instrument natif pour détecter les changements live."""
        if getattr(track, "track_type", "midi") != "midi":
            return None
        target_track = None
        if getattr(track, "synth_route_track_id", None) and self.project:
            target_track = self.project.get_track(track.synth_route_track_id)
        inst = self.get_native_instrument(target_track) if target_track else self.get_native_instrument(track)
        if inst is None:
            return None
        ptype = getattr(inst, "plugin_type_id", None)
        route_sig = (getattr(track, "synth_output_bus", None), getattr(track, "synth_route_track_id", None))
        if ptype == "novadaw.synth":
            d = getattr(inst, "delay", None)
            r = getattr(inst, "reverb", None)
            return (
                route_sig,
                getattr(inst, "enabled", True),
                round(getattr(inst, "master_volume", 1.0), 4),
                getattr(inst, "preset_name", ""),
                (d.enabled, round(d.time_sec, 4), round(d.feedback, 4), bool(getattr(d, "ping_pong", True)), round(d.mix, 4)) if d else None,
                (r.enabled, round(r.room_size, 4), round(r.damping, 4), round(getattr(r, "width", 1.0), 4), round(r.mix, 4)) if r else None,
                tuple(
                    (
                        l.enabled, l.muted, l.soloed, l.waveform,
                        round(l.volume, 4), round(l.pan, 4), l.octave, l.semitone, round(l.fine_tune, 2),
                        round(getattr(l, "pulse_width", 0.5), 3),
                        round(getattr(l, "fm_ratio", 2.0), 3), round(getattr(l, "fm_depth", 1.0), 3),
                        round(getattr(l, "unison_detune", 0.25), 3), round(getattr(l, "unison_spread", 0.8), 3),
                        l.filter_type, round(l.cutoff, 1), round(l.resonance, 3), round(l.drive, 3),
                        round(getattr(l, "filter_env_amount", 0.0), 3),
                        round(getattr(l, "filter_attack", 0.015), 4), round(getattr(l, "filter_decay", 0.3), 4),
                        round(getattr(l, "filter_sustain", 0.2), 4), round(getattr(l, "filter_release", 0.4), 4),
                        round(l.attack, 4), round(l.decay, 4), round(l.sustain, 4), round(l.release, 4),
                        l.lfo_enabled, getattr(l, "lfo_waveform", "sine"),
                        round(l.lfo_rate, 3), round(l.lfo_depth, 3), l.lfo_target,
                        l.output_bus
                    )
                    for l in getattr(inst, "layers", [])
                ),
            )
        if ptype == "novadaw.piano":
            return (route_sig, tuple(inst.get_state().items()))
        if ptype == "novadaw.guitar":
            return (
                route_sig,
                getattr(inst, "enabled", True),
                getattr(inst, "preset_name", ""),
                getattr(inst, "guitar_mode", "acoustic_steel"),
                round(getattr(inst, "string_tension", 0.62), 4),
                round(getattr(inst, "pluck_position", 0.22), 4),
                round(getattr(inst, "brightness", 0.72), 4),
                round(getattr(inst, "sustain_sec", 4.8), 4),
                round(getattr(inst, "release_sec", 0.24), 4),
                round(getattr(inst, "pick_attack", 0.60), 4),
                round(getattr(inst, "palm_mute", 0.0), 4),
                round(getattr(inst, "body_resonance", 0.75), 4),
                round(getattr(inst, "pickup_position", 0.22), 4),
                round(getattr(inst, "stereo_width", 0.35), 4),
                round(getattr(inst, "reverb_mix", 0.16), 4),
                round(getattr(inst, "reverb_room", 0.45), 4),
                bool(getattr(inst, "dual_polarization", True)),
                round(getattr(inst, "master_volume", 0.95), 4),
            )
        if ptype == "novadaw.drum_machine":
            r = getattr(inst, "reverb", None)
            return (
                route_sig,
                getattr(inst, "enabled", True),
                round(getattr(inst, "master_volume", 1.0), 4),
                getattr(inst, "preset_name", ""),
                (r.enabled, round(r.room_size, 4), round(r.damping, 4), round(getattr(r, "width", 1.0), 4), round(r.wet_mix, 4)) if r else None,
                tuple(
                    (
                        p.muted, p.soloed, round(p.volume, 4), round(p.pan, 4),
                        round(p.tune, 2), round(p.decay, 4), round(p.reverb_send, 4), p.sample_filename
                    )
                    for p in getattr(inst, "pads", [])
                ),
            )
        return None

    def invalidate_all_track_caches(self, trigger_async: bool = False):
        """Vide l'intégralité du cache RAM ASIO-Guard."""
        self.invalidate_track_cache(track_id=None, trigger_async=trigger_async)

    def _schedule_debounce_caching(self, delay: float = 0.15):
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
        """Coalesce requests onto one background producer, including during playback."""
        if not self._asio_guard_enabled or not self.project:
            return
        with self._cache_job_lock:
            if self._cache_closed:
                return
            self._cache_request_pending = True
            self._cache_force_requested |= force
            self._cache_requested_seq += 1
            if self._cache_worker is not None:
                return
            self._cache_worker = threading.Thread(
                target=self._run_cache_worker, daemon=True,
                name="NovaDAW-AsioGuard-PreRender"
            )
            self._cache_worker.start()

    def _run_cache_worker(self):
        while True:
            with self._cache_job_lock:
                if self._cache_closed or not self._cache_request_pending:
                    self._cache_worker = None
                    with self._track_cache_lock:
                        self._cache_done_cond.notify_all()
                    return
                self._cache_request_pending = False
                force = self._cache_force_requested
                self._cache_force_requested = False
                seq = self._cache_requested_seq
            try:
                self._ensure_tracks_cached(force=force)
            except Exception as exc:
                print(f"[AudioEngine] Erreur pré-rendu: {exc}")
            finally:
                with self._track_cache_lock:
                    if seq > self._cache_completed_seq:
                        self._cache_completed_seq = seq
                    self._cache_done_cond.notify_all()

    def wait_for_track_cache(self, timeout: float = 30.0) -> bool:
        """Attend sans attente active que toutes les pistes éligibles soient pré-calculées en cache ASIO-Guard."""
        project = self.project
        if project is None or not self._asio_guard_enabled:
            return True
        deadline = time.perf_counter() + max(0.0, float(timeout))
        with self._cache_job_lock:
            target_seq = self._cache_requested_seq

        eligible_ids: List[str] = []
        for t in project.tracks:
            if getattr(t, "armed", False):
                continue
            target = None
            if getattr(t, "synth_route_track_id", None):
                target = project.get_track(t.synth_route_track_id)
            ref = target or t
            if t.track_type == "midi" and ref.plugin_path and not str(ref.plugin_path).startswith("novadaw.") and self.get_native_instrument(ref) is None:
                continue
            eligible_ids.append(t.id)

        with self._track_cache_lock:
            while True:
                if self._cache_closed or self.project is not project:
                    return False
                all_ready = (
                    self._cache_completed_seq >= target_seq
                    and not self._cache_request_pending
                    and all(
                        (tid in self._track_audio_caches)
                        and bool(self._track_audio_caches[tid].get("is_valid", False))
                        and bool(self._track_audio_caches[tid].get("fully_rendered", False))
                        for tid in eligible_ids
                    )
                )
                if all_ready:
                    return True
                rem = deadline - time.perf_counter()
                if rem <= 0.0:
                    return False
                self._cache_done_cond.wait(timeout=min(0.05, rem))

    @staticmethod
    def _midi_status(status, track):
        return status | (max(1, min(16, int(getattr(track, "midi_channel", 1)))) - 1)

    def _compute_track_generator_hash(self, track: Track, bpm: float, sample_rate: int) -> str:
        """Calcule l'empreinte du générateur seul (notes, clips, instrument) indépendamment des effets d'insert."""
        parts = [
            track.id,
            str(getattr(track, "midi_channel", 1)),
            str(track.plugin_path or ""),
            str(getattr(track, "synth_output_bus", None)),
            str(getattr(track, "synth_route_track_id", None)),
            f"{bpm:.2f}",
            str(sample_rate),
        ]

        target_track = None
        if getattr(track, "synth_route_track_id", None) and self.project:
            target_track = self.project.get_track(track.synth_route_track_id)
        native_inst = self.get_native_instrument(target_track) if target_track else self.get_native_instrument(track)
        if native_inst and hasattr(native_inst, "get_state"):
            try:
                parts.append(f"EN:{getattr(native_inst, 'enabled', True)}")
                parts.append(str(native_inst.get_state()))
            except Exception:
                pass

        for c in track.clips:
            if isinstance(c, MidiClip):
                parts.append(f"M:{c.id}:{c.start_beat:.3f}:{c.length_beats:.3f}:{len(c.notes)}")
                for n in c.notes:
                    parts.append(f"{n.pitch}:{n.start_beat:.3f}:{n.duration:.3f}:{n.velocity}")
            elif isinstance(c, AudioClip):
                gain_v = float(getattr(c, "gain", 1.0))
                off_v = float(getattr(c, "source_offset_beats", 0.0))
                data_len = len(c.audio_data) if getattr(c, "audio_data", None) is not None else 0
                parts.append(
                    f"A:{c.id}:{c.start_beat:.3f}:{c.length_beats:.3f}:{off_v:.3f}:{gain_v:.4f}:{data_len}:{getattr(c, 'file_path', '')}"
                )

        return hashlib.md5("".join(parts).encode("utf-8")).hexdigest()

    def _compute_track_hash(self, track: Track, bpm: float, sample_rate: int, gen_hash: Optional[str] = None) -> str:
        """Calcule l'empreinte complète (générateur + effets d'insert + automatisations plugin) pour ASIO-Guard."""
        base = gen_hash if gen_hash is not None else self._compute_track_generator_hash(track, bpm, sample_rate)
        fx_parts = [base]

        inserts = getattr(track, "insert_effects", None)
        if inserts:
            fx_parts.append("VST:" + "|".join(str(x) for x in inserts if x))

        target_track = None
        if getattr(track, "synth_route_track_id", None) and self.project:
            target_track = self.project.get_track(track.synth_route_track_id)
        native_inst = self.get_native_instrument(target_track) if target_track else self.get_native_instrument(track)

        for p in getattr(track, "plugins", []) or []:
            if self._is_non_insert_plugin(p, track, native_inst):
                continue
            try:
                st = p.get_state() if hasattr(p, "get_state") else {}
                fx_parts.append(f"FX:{getattr(p, 'plugin_type_id', '')}:{getattr(p, 'enabled', True)}:{st}")
            except Exception:
                fx_parts.append(f"FX:{id(p)}:{getattr(p, 'enabled', True)}")

        for lane in getattr(track, "automation_lanes", None) or []:
            if getattr(lane, "enabled", True) and getattr(lane, "points", None):
                if getattr(lane, "target_scope", "track") != "track":
                    fx_parts.append(f"AL:{lane.target_scope}:{lane.target_param}:{len(lane.points)}")

        if len(fx_parts) == 1:
            return base
        return hashlib.md5("".join(fx_parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _compute_native_release_tail_beats(native_inst: Any, is_drum: bool, beats_per_sec: float, default_beats: float) -> float:
        """Calcule la marge de queue en temps (beats) adaptée au paramètre release de l'instrument natif."""
        if is_drum:
            return 0.0
        if native_inst is not None and hasattr(native_inst, "get_release_tail_sec"):
            try:
                return max(default_beats, float(native_inst.get_release_tail_sec()) * max(0.2, float(beats_per_sec)))
            except Exception:
                pass
        return default_beats

    def _render_track_offline(self, track: Track, bpm: float, sample_rate: int, end_beat: float, progress_callback: Optional[Callable[[float], None]] = None) -> Optional[np.ndarray]:
        """Synchronous compatibility entry point; the worker consumes the same blocks."""
        buf = None
        for buf, _ready_frames in self._iter_track_offline(track, bpm, sample_rate, end_beat, progress_callback):
            pass
        return buf

    def _iter_track_offline(self, track: Track, bpm: float, sample_rate: int, end_beat: float, progress_callback: Optional[Callable[[float], None]] = None) -> Iterator[tuple[np.ndarray, int]]:
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
                if not getattr(native_inst, "enabled", True):
                    all_midi_notes = []
                else:
                    all_midi_notes = None
                is_drum = (getattr(native_inst, "plugin_type_id", None) == "novadaw.drum_machine" or
                           getattr(track, "plugin_path", None) == "novadaw.drum_machine")
                release_tail_beats = self._compute_native_release_tail_beats(native_inst, is_drum, beats_per_sec, 2.0)
                bus_idx = getattr(track, "synth_output_bus", None)

                # Pré-collecter toutes les notes MIDI de la piste une seule fois
                if all_midi_notes is None:
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

                # Préserver les états de lecture temps réel pendant le pré-rendu.
                iso_delay = None
                iso_reverb = None
                iso_svfs = None
                iso_guitar_ctx = None
                iso_piano_ctx = None
                if getattr(native_inst, "plugin_type_id", None) == "novadaw.piano":
                    iso_piano_ctx = native_inst.create_isolated_context(sample_rate)
                elif getattr(native_inst, "plugin_type_id", None) == "novadaw.guitar" and hasattr(native_inst, "create_isolated_context"):
                    try:
                        iso_guitar_ctx = native_inst.create_isolated_context(sample_rate)
                    except Exception:
                        iso_guitar_ctx = None
                elif getattr(native_inst, "plugin_type_id", None) == "novadaw.synth" and hasattr(native_inst, "_render_slice_internal"):
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

                # Small blocks retain DSP history and let other tracks prepare their start.
                chunk_frames = 4096
                # Mixed MIDI/audio tracks are published only after audio has been added.
                progressive = not any(isinstance(c, AudioClip) for c in track.clips)
                note_ends = [n.start_beat + n.duration + release_tail_beats for n in all_midi_notes]
                max_note_end_beat = max(note_ends, default=0.0)
                num_all_notes = len(all_midi_notes)
                scan_start = 0
                iso_piano_state = native_inst.get_state() if iso_piano_ctx is not None else None
                for f_idx in range(0, total_frames, chunk_frames):
                    if not all_midi_notes:
                        break
                    n = min(chunk_frames, total_frames - f_idx)
                    sb = (f_idx / sample_rate) * beats_per_sec
                    eb = ((f_idx + n) / sample_rate) * beats_per_sec

                    if progress_callback and total_frames > 0:
                        try:
                            progress_callback(min(1.0, f_idx / total_frames))
                        except Exception:
                            pass

                    while scan_start < num_all_notes and note_ends[scan_start] <= sb:
                        scan_start += 1
                    chunk_notes = []
                    for j in range(scan_start, num_all_notes):
                        cand = all_midi_notes[j]
                        if cand.start_beat >= eb:
                            break
                        if note_ends[j] > sb:
                            chunk_notes.append(cand)
                    has_active_drum_voices = is_drum and bool(getattr(native_inst, "_active_voices", None))

                    if not chunk_notes and not has_active_drum_voices and sb >= max_note_end_beat:
                        # Toutes les notes et leurs queues de résonance sont terminées : le reste du buffer est déjà à zéro
                        break

                    if chunk_notes or has_active_drum_voices:
                        try:
                            if iso_piano_ctx is not None:
                                rendered = native_inst.render_in_context(
                                    chunk_notes, sb, beats_per_sec, n, sample_rate, iso_piano_ctx, state=iso_piano_state
                                )
                            elif iso_guitar_ctx is not None:
                                rendered = native_inst._render_slice_internal(
                                    chunk_notes, sb, eb, beats_per_sec, n, sample_rate,
                                    isolated_ctx=iso_guitar_ctx,
                                    use_live_state=False
                                )
                            elif iso_delay is not None:
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
                            raise
                    if progressive:
                        yield buf, f_idx + n

            elif not track.plugin_path:
                # Synthétiseur polyphonique par défaut de NovaDAW (SynthVoice)
                for clip in track.clips:
                    if not isinstance(clip, MidiClip):
                        continue
                    cs_b = clip.start_beat
                    for note in clip.notes:
                        abs_start_b = cs_b + note.start_beat
                        if abs_start_b >= end_beat:
                            continue
                        note_dur_sec = note.duration / beats_per_sec
                        wave = SynthVoice.generate_note(note.pitch, note_dur_sec, sample_rate, note.velocity)
                        abs_start_sec = abs_start_b / beats_per_sec
                        start_sample = int(abs_start_sec * sample_rate)
                        wave_offset = 0
                        buf_start = start_sample
                        if buf_start < 0:
                            wave_offset = -buf_start
                            buf_start = 0
                        if buf_start < total_frames and wave_offset < len(wave):
                            to_copy = min(len(wave) - wave_offset, total_frames - buf_start)
                            if to_copy > 0:
                                buf[buf_start:buf_start + to_copy] += wave[wave_offset:wave_offset + to_copy]

        # 2. Rendu des clips Audio
        for clip in track.clips:
            if not isinstance(clip, AudioClip) or clip.audio_data is None:
                continue
            cs_b = clip.start_beat
            dur_sec = clip.length_beats / beats_per_sec
            clip_frames = int(dur_sec * sample_rate)
            dest_start = int((cs_b / beats_per_sec) * sample_rate)
            if dest_start < total_frames:
                src_off_b = max(0.0, float(getattr(clip, "source_offset_beats", 0.0)))
                src_offset_frames = int((src_off_b / beats_per_sec) * sample_rate)
                src_samples = clip.audio_data
                if src_offset_frames >= len(src_samples):
                    continue
                avail_src = src_samples[src_offset_frames:]
                to_copy = min(len(avail_src), clip_frames, total_frames - dest_start)
                if to_copy > 0:
                    if avail_src.ndim == 1:
                        buf[dest_start:dest_start + to_copy, 0] += avail_src[:to_copy] * clip.gain
                        buf[dest_start:dest_start + to_copy, 1] += avail_src[:to_copy] * clip.gain
                    else:
                        buf[dest_start:dest_start + to_copy] += avail_src[:to_copy, :2] * clip.gain

        # Les inserts de piste (Égaliseur, Compresseur, VST) sont traités en temps réel
        # sur le buffer générateur pré-calculé, garantissant une réactivité immédiate sans re-rendu offline.

        if progress_callback:
            try:
                progress_callback(1.0)
            except Exception:
                pass

        yield buf, total_frames

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

    def _can_bake_track_inserts(self, track: Track, native_inst: Optional[Any]) -> bool:
        """Indique si la chaîne d'inserts de la piste est statique/clonable et peut être pré-calculée dans ASIO-Guard."""
        if getattr(track, "insert_effects", None) and any(track.insert_effects):
            return False
        for lane in getattr(track, "automation_lanes", None) or []:
            if getattr(lane, "enabled", True) and getattr(lane, "points", None):
                if getattr(lane, "target_scope", "track") != "track":
                    return False
        for p in getattr(track, "plugins", []) or []:
            if self._is_non_insert_plugin(p, track, native_inst):
                continue
            if not getattr(p, "enabled", True):
                continue
            ptype = str(getattr(p, "plugin_type_id", "") or "")
            if not ptype.startswith("novadaw.") or ptype == "novadaw.vocal_glide":
                return False
            if getattr(p, "supports_automation_write", False):
                return False
        return True

    def _snapshot_cache_track(self, track: Track) -> Optional[Track]:
        """Freeze notes, native instrument, and optional static insert FX without touching live DSP state."""
        snapshot = copy.copy(track)
        copied_clips = []
        for c in list(track.clips):
            if isinstance(c, MidiClip):
                mc = copy.copy(c)
                mc.notes = [
                    MidiNote(pitch=n.pitch, start_beat=n.start_beat, duration=n.duration, velocity=n.velocity)
                    for n in c.notes
                ]
                copied_clips.append(mc)
            else:
                copied_clips.append(copy.copy(c))
        snapshot.clips = copied_clips

        target = None
        if getattr(track, "synth_route_track_id", None) and self.project:
            target = self.project.get_track(track.synth_route_track_id)
        native = self.get_native_instrument(target or track)
        snapshot.plugins = []  # Inserts run live in C99 (<0.1 ms total) for 0 ms knob responsiveness.
        snapshot._baked_insert_fx = []
        snapshot._includes_inserts = False
        if native is not None:
            from plugins.registry import plugin_registry
            isolated = plugin_registry.create_plugin(native.plugin_type_id)
            if isolated is None:
                return None
            isolated.set_state(copy.deepcopy(native.get_state()))
            isolated.enabled = native.enabled
            if hasattr(native, "preset_name"):
                try:
                    isolated.preset_name = native.preset_name
                except Exception:
                    pass
            snapshot.plugins = [isolated]
            snapshot.plugin_path = native.plugin_type_id
            snapshot.synth_route_track_id = None
        elif track.track_type == "midi" and (target or track).plugin_path:
            # A silent cache must never replace an external instrument's live output.
            return None

        if self._can_bake_track_inserts(track, native):
            # If the track has zero active insert FX, generator output IS already final post-FX output.
            has_any_active_insert = any(
                not self._is_non_insert_plugin(p, track, native) and getattr(p, "enabled", True)
                for p in (getattr(track, "plugins", None) or [])
            )
            if not has_any_active_insert:
                snapshot._includes_inserts = True

        return snapshot

    @staticmethod
    def _apply_baked_fx_range(
        dry_buf: np.ndarray,
        out_buf: np.ndarray,
        start_f: int,
        end_f: int,
        fx_clones: List[Any],
        sample_rate: int,
        chunk_frames: int = 8192,
    ) -> None:
        """Applique les clones d'inserts C natifs avec auto-bypass -100 dB sur les zones silencieuses."""
        if not fx_clones:
            if out_buf is not dry_buf:
                out_buf[start_f:end_f] = dry_buf[start_f:end_f]
            return
        for fx in fx_clones:
            if hasattr(fx, "reset"):
                try:
                    fx.reset()
                except Exception:
                    pass

        active = False
        silent_frames = 0
        hold_frames = max(256, int(0.020 * sample_rate))
        pos = start_f
        while pos < end_f:
            n = min(chunk_frames, end_f - pos)
            dry_slice = dry_buf[pos:pos + n]
            dry_pk = float(np.max(np.abs(dry_slice))) if n > 0 else 0.0
            if dry_pk <= 1e-5 and not active:
                out_buf[pos:pos + n].fill(0.0)
                pos += n
                continue
            if dry_pk > 1e-5:
                active = True
                silent_frames = 0
            work = dry_slice.copy()
            for fx in fx_clones:
                try:
                    res = fx.process(work, sample_rate)
                    work = AudioEngine._normalize_plugin_output(res, work, n)
                except Exception:
                    pass
            if dry_pk <= 1e-5:
                fx_pk = float(np.max(np.abs(work))) if n > 0 else 0.0
                if fx_pk <= 1e-5:
                    silent_frames += n
                    if silent_frames >= hold_frames:
                        active = False
                        silent_frames = 0
                        for fx in fx_clones:
                            if hasattr(fx, "reset"):
                                try:
                                    fx.reset()
                                except Exception:
                                    pass
                        work.fill(0.0)
                else:
                    silent_frames = 0
            out_buf[pos:pos + n] = work
            pos += n

    def _render_generator_range_into(
        self,
        snapshot: Track,
        dry_buf: np.ndarray,
        start_f: int,
        end_f: int,
        bpm: float,
        sample_rate: int,
        end_beat: float,
        abort_check: Callable[[], bool],
        on_chunk_ready: Optional[Callable[[int], None]] = None,
    ) -> bool:
        """Rend le générateur MIDI/Audio de `snapshot` sur `[start_f, end_f)` avec fenêtrage O(1) et gros blocs C."""
        if start_f >= end_f:
            return not abort_check()
        beats_per_sec = bpm / 60.0
        total_frames = len(dry_buf)

        if snapshot.track_type == "midi":
            native_inst = self.get_native_instrument(snapshot)
            if native_inst and hasattr(native_inst, "render_slice"):
                if getattr(native_inst, "enabled", True):
                    is_drum = (
                        getattr(native_inst, "plugin_type_id", None) == "novadaw.drum_machine"
                        or getattr(snapshot, "plugin_path", None) == "novadaw.drum_machine"
                    )
                    release_tail_beats = self._compute_native_release_tail_beats(native_inst, is_drum, beats_per_sec, 2.0)
                    bus_idx = getattr(snapshot, "synth_output_bus", None)

                    all_midi_notes: List[MidiNote] = []
                    for clip in snapshot.clips:
                        if not isinstance(clip, MidiClip):
                            continue
                        cs = clip.start_beat
                        for note in clip.notes:
                            all_midi_notes.append(
                                MidiNote(
                                    pitch=note.pitch,
                                    start_beat=cs + note.start_beat,
                                    duration=note.duration,
                                    velocity=note.velocity,
                                )
                            )
                    all_midi_notes.sort(key=lambda mn: mn.start_beat)
                    note_ends = [mn.start_beat + mn.duration + release_tail_beats for mn in all_midi_notes]
                    max_note_end_beat = max(note_ends, default=0.0)
                    num_notes = len(all_midi_notes)

                    ptype = getattr(native_inst, "plugin_type_id", None)
                    iso_piano_ctx = None
                    iso_piano_state = None
                    iso_guitar_ctx = None
                    iso_delay = None
                    iso_reverb = None
                    iso_svfs = None
                    if ptype == "novadaw.piano":
                        iso_piano_ctx = native_inst.create_isolated_context(sample_rate)
                        iso_piano_state = native_inst.get_state()
                    elif ptype == "novadaw.guitar" and hasattr(native_inst, "create_isolated_context"):
                        try:
                            iso_guitar_ctx = native_inst.create_isolated_context(sample_rate)
                        except Exception:
                            iso_guitar_ctx = None
                    elif ptype == "novadaw.synth" and hasattr(native_inst, "_render_slice_internal"):
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

                    scan_start = 0
                    f_idx = start_f
                    chunk_idx = 0
                    bulk_step = 8192
                    while f_idx < end_f:
                        if abort_check():
                            return False
                        step = 4096 if chunk_idx < 2 else bulk_step
                        n = min(step, end_f - f_idx)
                        sb = (f_idx / sample_rate) * beats_per_sec
                        eb = ((f_idx + n) / sample_rate) * beats_per_sec

                        if num_notes == 0:
                            if on_chunk_ready is not None:
                                on_chunk_ready(end_f)
                            break

                        while scan_start < num_notes and note_ends[scan_start] <= sb:
                            scan_start += 1
                        chunk_notes = []
                        for j in range(scan_start, num_notes):
                            cand = all_midi_notes[j]
                            if cand.start_beat >= eb:
                                break
                            if note_ends[j] > sb:
                                chunk_notes.append(cand)

                        has_active_drum_voices = is_drum and bool(getattr(native_inst, "_active_voices", None))
                        if not chunk_notes and not has_active_drum_voices:
                            if sb >= max_note_end_beat or scan_start >= num_notes:
                                if on_chunk_ready is not None:
                                    on_chunk_ready(end_f)
                                break
                            # Sauter directement jusqu'au prochain événement MIDI (aligné sur 4096 frames)
                            next_sb = all_midi_notes[scan_start].start_beat
                            next_f = (int((next_sb / beats_per_sec) * sample_rate) // 4096) * 4096
                            if next_f > f_idx + n:
                                f_idx = min(end_f, next_f)
                                chunk_idx += 1
                                if on_chunk_ready is not None:
                                    on_chunk_ready(f_idx)
                                continue

                        if chunk_notes or has_active_drum_voices:
                            if iso_piano_ctx is not None:
                                rendered = native_inst.render_in_context(
                                    chunk_notes, sb, beats_per_sec, n, sample_rate, iso_piano_ctx, state=iso_piano_state
                                )
                            elif iso_guitar_ctx is not None:
                                rendered = native_inst._render_slice_internal(
                                    chunk_notes, sb, eb, beats_per_sec, n, sample_rate,
                                    isolated_ctx=iso_guitar_ctx,
                                    use_live_state=False,
                                )
                            elif iso_delay is not None:
                                rendered = native_inst._render_slice_internal(
                                    chunk_notes, sb, eb, beats_per_sec, n, sample_rate,
                                    bus_index=bus_idx,
                                    target_delay=iso_delay,
                                    target_reverb=iso_reverb,
                                    voice_svf_dict=iso_svfs,
                                    use_live_state=False,
                                )
                            elif bus_idx is not None:
                                try:
                                    rendered = native_inst.render_slice(
                                        chunk_notes, sb, eb, beats_per_sec, n, sample_rate, bus_index=bus_idx
                                    )
                                except TypeError:
                                    rendered = native_inst.render_slice(
                                        chunk_notes, sb, eb, beats_per_sec, n, sample_rate
                                    )
                            else:
                                rendered = native_inst.render_slice(
                                    chunk_notes, sb, eb, beats_per_sec, n, sample_rate
                                )
                            if rendered is not None and len(rendered) > 0:
                                dry_buf[f_idx:f_idx + n] = rendered[:n]

                        f_idx += n
                        chunk_idx += 1
                        if on_chunk_ready is not None:
                            on_chunk_ready(f_idx)

            elif not snapshot.plugin_path:
                for clip in snapshot.clips:
                    if abort_check():
                        return False
                    if not isinstance(clip, MidiClip):
                        continue
                    cs_b = clip.start_beat
                    for note in clip.notes:
                        abs_start_b = cs_b + note.start_beat
                        if abs_start_b >= end_beat:
                            continue
                        note_dur_sec = note.duration / beats_per_sec
                        wave = SynthVoice.generate_note(note.pitch, note_dur_sec, sample_rate, note.velocity)
                        start_sample = int((abs_start_b / beats_per_sec) * sample_rate)
                        seg_start = max(start_f, start_sample)
                        seg_end = min(end_f, total_frames, start_sample + len(wave))
                        if seg_end > seg_start:
                            w_off = seg_start - start_sample
                            dry_buf[seg_start:seg_end] += wave[w_off:w_off + (seg_end - seg_start)]

        # Clips Audio sur [start_f, end_f)
        for clip in snapshot.clips:
            if not isinstance(clip, AudioClip) or clip.audio_data is None:
                continue
            cs_b = clip.start_beat
            dur_sec = clip.length_beats / beats_per_sec
            clip_frames = int(dur_sec * sample_rate)
            dest_start = int((cs_b / beats_per_sec) * sample_rate)
            src_off_b = max(0.0, float(getattr(clip, "source_offset_beats", 0.0)))
            src_offset_frames = int((src_off_b / beats_per_sec) * sample_rate)
            src_samples = clip.audio_data
            if src_offset_frames >= len(src_samples):
                continue
            avail_len = min(len(src_samples) - src_offset_frames, clip_frames, total_frames - dest_start)
            if avail_len <= 0:
                continue
            seg_start = max(start_f, dest_start)
            seg_end = min(end_f, dest_start + avail_len)
            if seg_end > seg_start:
                s_idx = src_offset_frames + (seg_start - dest_start)
                count = seg_end - seg_start
                sub = src_samples[s_idx:s_idx + count]
                if sub.ndim == 1:
                    dry_buf[seg_start:seg_end, 0] += sub * clip.gain
                    dry_buf[seg_start:seg_end, 1] += sub * clip.gain
                else:
                    dry_buf[seg_start:seg_end] += sub[:, :2] * clip.gain

        return not abort_check()

    def _render_track_cache_job(
        self,
        project: Project,
        track: Track,
        snapshot: Track,
        gen_hash: str,
        thash: str,
        track_gen: int,
        fx_gen: int,
        inst_sig: Any,
        bpm: float,
        sample_rate: int,
        end_beat: float,
        total_frames: int,
        playhead_beat: float,
        generation: int,
        reusable_dry_buf: Optional[np.ndarray] = None,
    ) -> None:
        """Exécute le pré-rendu parallèle orienté tête de lecture d'une piste + inserts statiques."""
        tid = track.id

        def _aborted() -> bool:
            return (
                self._cache_closed
                or self.project is not project
                or generation != self._cache_generation
                or self.sample_rate != sample_rate
                or project.bpm != bpm
                or project.get_track(tid) is not track
                or getattr(track, "armed", False)
                or self._track_generations.get(tid, 0) != track_gen
                or self._track_fx_generations.get(tid, 0) != fx_gen
            )

        if _aborted():
            return

        baked_fx: List[Any] = getattr(snapshot, "_baked_insert_fx", None) or []
        includes_inserts: bool = bool(getattr(snapshot, "_includes_inserts", False))
        has_audio_clips = any(isinstance(c, AudioClip) for c in snapshot.clips)
        progressive = not has_audio_clips

        beats_per_sec = bpm / 60.0
        if playhead_beat > 6.0 and playhead_beat < end_beat and reusable_dry_buf is None:
            preroll_beat = max(0.0, playhead_beat - 4.0)
            start_frame = min(total_frames, (int((preroll_beat / beats_per_sec) * sample_rate) // 4096) * 4096)
        else:
            start_frame = 0

        if reusable_dry_buf is not None and len(reusable_dry_buf) >= total_frames:
            dry_buf = reusable_dry_buf[:total_frames]
            out_buf = np.zeros((total_frames, 2), dtype=np.float32) if baked_fx else dry_buf
            if baked_fx:
                # Publier d'abord la fenêtre proche de la tête de lecture puis le reste
                if playhead_beat > 6.0 and playhead_beat < end_beat:
                    fx_start = min(total_frames, (int((max(0.0, playhead_beat - 2.0) / beats_per_sec) * sample_rate) // 4096) * 4096)
                else:
                    fx_start = 0
                self._apply_baked_fx_range(dry_buf, out_buf, fx_start, total_frames, baked_fx, sample_rate)
                if _aborted():
                    return
                if fx_start > 0:
                    with self._track_cache_lock:
                        if not _aborted():
                            self._track_audio_caches[tid] = {
                                "audio": out_buf,
                                "bpm": bpm,
                                "sample_rate": sample_rate,
                                "total_beats": end_beat,
                                "version_hash": thash,
                                "track_gen": track_gen,
                                "fx_gen": fx_gen,
                                "inst_sig": inst_sig,
                                "includes_inserts": includes_inserts,
                                "valid_start_frame": fx_start,
                                "rendered_frames": total_frames,
                                "fully_rendered": False,
                                "is_valid": True,
                            }
                    head_clones = [self._clone_native_plugin(p) for p in baked_fx]
                    if all(c is not None for c in head_clones):
                        head_out = np.zeros((fx_start, 2), dtype=np.float32)
                        self._apply_baked_fx_range(dry_buf, head_out, 0, fx_start, head_clones, sample_rate)
                        out_buf[:fx_start] = head_out
            with self._track_cache_lock:
                if _aborted():
                    return
                self._track_audio_caches[tid] = {
                    "audio": out_buf,
                    "bpm": bpm,
                    "sample_rate": sample_rate,
                    "total_beats": end_beat,
                    "version_hash": thash,
                    "track_gen": track_gen,
                    "fx_gen": fx_gen,
                    "inst_sig": inst_sig,
                    "includes_inserts": includes_inserts,
                    "valid_start_frame": 0,
                    "rendered_frames": total_frames,
                    "fully_rendered": True,
                    "is_valid": True,
                }
                self._cache_done_cond.notify_all()
            return

        dry_buf = np.zeros((total_frames, 2), dtype=np.float32)
        out_buf = np.zeros((total_frames, 2), dtype=np.float32) if baked_fx else dry_buf

        fx_state = {"active": False, "silent_frames": 0, "last_pos": start_frame}
        hold_frames = max(256, int(0.020 * sample_rate))
        for fx in baked_fx:
            if hasattr(fx, "reset"):
                try:
                    fx.reset()
                except Exception:
                    pass

        pub_state = {"count": 0, "dry_entry": None, "out_entry": None}

        def _publish_chunk(ready_end: int) -> None:
            if baked_fx:
                p0 = fx_state["last_pos"]
                if ready_end > p0:
                    n_seg = ready_end - p0
                    dry_slice = dry_buf[p0:ready_end]
                    dry_pk = float(np.max(np.abs(dry_slice))) if n_seg > 0 else 0.0
                    if dry_pk <= 1e-5 and not fx_state["active"]:
                        out_buf[p0:ready_end].fill(0.0)
                    else:
                        if dry_pk > 1e-5:
                            fx_state["active"] = True
                            fx_state["silent_frames"] = 0
                        work = dry_slice.copy()
                        for fx in baked_fx:
                            try:
                                res = fx.process(work, sample_rate)
                                work = self._normalize_plugin_output(res, work, n_seg)
                            except Exception:
                                pass
                        if dry_pk <= 1e-5:
                            fx_pk = float(np.max(np.abs(work))) if n_seg > 0 else 0.0
                            if fx_pk <= 1e-5:
                                fx_state["silent_frames"] += n_seg
                                if fx_state["silent_frames"] >= hold_frames:
                                    fx_state["active"] = False
                                    fx_state["silent_frames"] = 0
                                    for fx in baked_fx:
                                        if hasattr(fx, "reset"):
                                            try:
                                                fx.reset()
                                            except Exception:
                                                pass
                                    work.fill(0.0)
                            else:
                                fx_state["silent_frames"] = 0
                        out_buf[p0:ready_end] = work
                    fx_state["last_pos"] = ready_end

            if not progressive:
                return
            c_idx = pub_state["count"]
            pub_state["count"] = c_idx + 1
            is_complete = (start_frame == 0 and ready_end >= total_frames)

            if pub_state["out_entry"] is not None and not is_complete:
                # Mise à jour atomique en place de rendered_frames à chaque bloc (coût ~10 ns, zéro cache miss)
                if not _aborted():
                    pub_state["dry_entry"]["rendered_frames"] = ready_end
                    pub_state["out_entry"]["rendered_frames"] = ready_end
                return

            with self._track_cache_lock:
                if _aborted():
                    return
                prev = self._track_audio_caches.get(tid)
                if (
                    start_frame == 0
                    and prev is not None
                    and prev.get("is_valid", False)
                    and prev.get("version_hash") == thash
                    and prev.get("valid_start_frame", 0) == 0
                    and prev.get("rendered_frames", 0) > ready_end
                ):
                    return
                dry_entry = {
                    "audio": dry_buf,
                    "bpm": bpm,
                    "sample_rate": sample_rate,
                    "total_beats": end_beat,
                    "gen_hash": gen_hash,
                    "track_gen": track_gen,
                    "inst_sig": inst_sig,
                    "valid_start_frame": start_frame,
                    "rendered_frames": ready_end,
                    "fully_rendered": is_complete,
                    "is_valid": True,
                }
                out_entry = {
                    "audio": out_buf,
                    "bpm": bpm,
                    "sample_rate": sample_rate,
                    "total_beats": end_beat,
                    "version_hash": thash,
                    "track_gen": track_gen,
                    "fx_gen": fx_gen,
                    "inst_sig": inst_sig,
                    "includes_inserts": includes_inserts,
                    "valid_start_frame": start_frame,
                    "rendered_frames": ready_end,
                    "fully_rendered": is_complete,
                    "is_valid": True,
                }
                pub_state["dry_entry"] = dry_entry
                pub_state["out_entry"] = out_entry
                self._track_dry_caches[tid] = dry_entry
                self._track_audio_caches[tid] = out_entry
                if is_complete:
                    self._cache_done_cond.notify_all()

        # Phase A : de la tête de lecture (avec pré-roll) jusqu'à la fin du morceau
        ok = self._render_generator_range_into(
            snapshot, dry_buf, start_frame, total_frames, bpm, sample_rate, end_beat,
            abort_check=_aborted,
            on_chunk_ready=_publish_chunk if progressive else None,
        )
        if not ok or _aborted():
            return

        if not progressive and baked_fx:
            self._apply_baked_fx_range(dry_buf, out_buf, start_frame, total_frames, baked_fx, sample_rate)
            if _aborted():
                return

        # Phase B : backfill de [0, start_frame) sans perturber la lecture en cours sur [start_frame, total_frames)
        if start_frame > 0:
            head_snapshot = self._snapshot_cache_track(track)
            if head_snapshot is None or _aborted():
                return
            head_dry = np.zeros((start_frame, 2), dtype=np.float32)
            ok_head = self._render_generator_range_into(
                head_snapshot, head_dry, 0, start_frame, bpm, sample_rate, end_beat,
                abort_check=_aborted,
                on_chunk_ready=None,
            )
            if not ok_head or _aborted():
                return
            head_fx = getattr(head_snapshot, "_baked_insert_fx", None) or []
            if head_fx:
                head_out = np.zeros((start_frame, 2), dtype=np.float32)
                self._apply_baked_fx_range(head_dry, head_out, 0, start_frame, head_fx, sample_rate)
                if _aborted():
                    return
                dry_buf[:start_frame] = head_dry
                out_buf[:start_frame] = head_out
            else:
                dry_buf[:start_frame] = head_dry

        with self._track_cache_lock:
            if _aborted():
                return
            self._track_dry_caches[tid] = {
                "audio": dry_buf,
                "bpm": bpm,
                "sample_rate": sample_rate,
                "total_beats": end_beat,
                "gen_hash": gen_hash,
                "track_gen": track_gen,
                "inst_sig": inst_sig,
                "valid_start_frame": 0,
                "rendered_frames": total_frames,
                "fully_rendered": True,
                "is_valid": True,
            }
            self._track_audio_caches[tid] = {
                "audio": out_buf,
                "bpm": bpm,
                "sample_rate": sample_rate,
                "total_beats": end_beat,
                "version_hash": thash,
                "track_gen": track_gen,
                "fx_gen": fx_gen,
                "inst_sig": inst_sig,
                "includes_inserts": includes_inserts,
                "valid_start_frame": 0,
                "rendered_frames": total_frames,
                "fully_rendered": True,
                "is_valid": True,
            }
            self._cache_done_cond.notify_all()

    def _ensure_tracks_cached(self, force: bool = False):
        """Pré-calcule les pistes éligibles (parallèle sans GIL en production, round-robin si _iter_track_offline est mocké)."""
        with self._cache_render_lock:
            project = self.project
            if project is None or self._cache_closed:
                return
            generation = self._cache_generation
            bpm = max(20.0, project.bpm)
            sample_rate = self.sample_rate
            max_beat = max(16.0, project.loop_end_beat if project.loop_enabled else 0.0)
            for track in project.tracks:
                for clip in track.clips:
                    max_beat = max(max_beat, clip.start_beat + clip.length_beats)
            end_beat = max_beat + 4.0
            for track in project.tracks:
                if track.plugin_path == "novadaw.piano":
                    piano = self.get_native_instrument(track)
                    if piano is not None:
                        tail_beats = piano.get_release_tail_sec() * bpm / 60.0
                        for clip in track.clips:
                            if isinstance(clip, MidiClip):
                                end_beat = max(end_beat, clip.start_beat + clip.length_beats + tail_beats)
            total_frames = max(1024, int(end_beat / (bpm / 60.0) * sample_rate))

            # Chemin de compatibilité exacte lorsque _iter_track_offline est monkeypatché dans les tests unitaires
            if "_iter_track_offline" in self.__dict__:
                jobs = []
                for track in sorted(list(project.tracks), key=lambda t: (not t.soloed, t.muted)):
                    if track.armed:
                        continue
                    thash = self._compute_track_hash(track, bpm, sample_rate)
                    with self._track_cache_lock:
                        cache = self._track_audio_caches.get(track.id)
                        if (not force and cache is not None and cache.get("is_valid", False)
                                and cache.get("fully_rendered", False)
                                and cache.get("version_hash") == thash and cache.get("audio") is not None
                                and cache.get("total_beats", 0) >= end_beat):
                            continue
                        if cache is not None and cache.get("version_hash") != thash:
                            self._track_audio_caches.pop(track.id, None)
                    snapshot = self._snapshot_cache_track(track)
                    if snapshot is not None:
                        jobs.append((track, thash, self._iter_track_offline(snapshot, bpm, sample_rate, end_beat)))
                total_jobs = len(jobs)
                completed = 0
                while jobs:
                    pending = []
                    for track, thash, blocks in jobs:
                        if (self._cache_closed or self.project is not project
                                or generation != self._cache_generation
                                or self.sample_rate != sample_rate or project.bpm != bpm):
                            return
                        if project.get_track(track.id) is not track or track.armed:
                            completed += 1
                            continue
                        try:
                            buf, ready_frames = next(blocks)
                        except StopIteration:
                            completed += 1
                            continue
                        except Exception as exc:
                            with self._track_cache_lock:
                                self._track_audio_caches.pop(track.id, None)
                            print(f"[AudioEngine] Erreur cache {track.name}: {exc}")
                            completed += 1
                            continue
                        with self._track_cache_lock:
                            if generation != self._cache_generation or self.project is not project:
                                return
                            if project.get_track(track.id) is not track:
                                completed += 1
                                continue
                            previous = self._track_audio_caches.get(track.id)
                            previous_ready = 0
                            if (previous and previous.get("is_valid", False)
                                    and previous.get("version_hash") == thash):
                                previous_ready = previous.get("rendered_frames", 0)
                            if previous_ready <= ready_frames:
                                self._track_audio_caches[track.id] = {
                                    "audio": buf, "bpm": bpm, "sample_rate": sample_rate,
                                    "total_beats": end_beat, "version_hash": thash,
                                    "track_gen": self._track_generations.get(track.id, 0),
                                    "fx_gen": self._track_fx_generations.get(track.id, 0),
                                    "inst_sig": self._fast_inst_signature(track),
                                    "includes_inserts": False,
                                    "valid_start_frame": 0,
                                    "is_valid": True, "rendered_frames": ready_frames,
                                    "fully_rendered": ready_frames >= total_frames,
                                }
                                if ready_frames >= total_frames:
                                    self._cache_done_cond.notify_all()
                        progress = (completed + ready_frames / total_frames * len(jobs)) / max(1, total_jobs)
                        self._report_caching_progress(completed, max(1, total_jobs), track.name, min(progress, 0.999))
                        if ready_frames < total_frames:
                            pending.append((track, thash, blocks))
                        else:
                            completed += 1
                        time.sleep(0)
                    jobs = pending
                with self._track_cache_lock:
                    self._cache_done_cond.notify_all()
                self._report_caching_progress(total_jobs, max(1, total_jobs), "Prêt", 1.0)
                return

            # Chemin haute performance parallèle (instruments les plus lourds d'abord : Piano -> Synth -> Guitar -> Drums)
            def _priority_key(t: Track):
                pp = str(getattr(t, "plugin_path", "") or "")
                w = 0 if pp == "novadaw.piano" else (1 if pp == "novadaw.synth" else (2 if pp == "novadaw.guitar" else 3))
                return (not getattr(t, "soloed", False), getattr(t, "muted", False), w)

            playhead_beat = max(0.0, float(self.current_beat))
            parallel_specs = []
            for track in sorted(list(project.tracks), key=_priority_key):
                if track.armed:
                    continue
                tid = track.id
                gen_hash = self._compute_track_generator_hash(track, bpm, sample_rate)
                thash = self._compute_track_hash(track, bpm, sample_rate, gen_hash=gen_hash)
                reusable_dry = None
                with self._track_cache_lock:
                    cache = self._track_audio_caches.get(tid)
                    if (
                        not force
                        and cache is not None
                        and cache.get("is_valid", False)
                        and cache.get("fully_rendered", False)
                        and cache.get("version_hash") == thash
                        and cache.get("audio") is not None
                        and cache.get("total_beats", 0) >= end_beat
                    ):
                        continue
                    if cache is not None and cache.get("version_hash") != thash:
                        self._track_audio_caches.pop(tid, None)
                    dry_cache = self._track_dry_caches.get(tid)
                    if (
                        not force
                        and dry_cache is not None
                        and dry_cache.get("is_valid", False)
                        and dry_cache.get("fully_rendered", False)
                        and dry_cache.get("gen_hash") == gen_hash
                        and dry_cache.get("bpm") == bpm
                        and dry_cache.get("sample_rate") == sample_rate
                        and dry_cache.get("total_beats", 0) >= end_beat
                        and dry_cache.get("audio") is not None
                    ):
                        reusable_dry = dry_cache["audio"]
                    track_gen = self._track_generations.get(tid, 0)
                    fx_gen = self._track_fx_generations.get(tid, 0)

                snapshot = self._snapshot_cache_track(track)
                if snapshot is None:
                    continue
                inst_sig = self._fast_inst_signature(track)
                parallel_specs.append((track, snapshot, gen_hash, thash, track_gen, fx_gen, inst_sig, reusable_dry))

            total_jobs = len(parallel_specs)
            if total_jobs == 0:
                with self._track_cache_lock:
                    self._cache_done_cond.notify_all()
                self._report_caching_progress(0, 1, "Prêt", 1.0)
                return

            max_workers = min(total_jobs, max(2, min(6, os.cpu_count() or 4)))
            completed_count = 0
            if max_workers <= 1:
                for spec in parallel_specs:
                    if self._cache_closed or self.project is not project or generation != self._cache_generation:
                        return
                    track = spec[0]
                    try:
                        self._render_track_cache_job(
                            project, track, spec[1], spec[2], spec[3], spec[4], spec[5], spec[6],
                            bpm, sample_rate, end_beat, total_frames, playhead_beat, generation,
                            reusable_dry_buf=spec[7],
                        )
                    except Exception as exc:
                        with self._track_cache_lock:
                            self._track_audio_caches.pop(track.id, None)
                        print(f"[AudioEngine] Erreur cache {track.name}: {exc}")
                    completed_count += 1
                    self._report_caching_progress(completed_count, total_jobs, track.name, completed_count / total_jobs)
            else:
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="NovaAsioGuard") as pool:
                    futures = {
                        pool.submit(
                            self._render_track_cache_job,
                            project, spec[0], spec[1], spec[2], spec[3], spec[4], spec[5], spec[6],
                            bpm, sample_rate, end_beat, total_frames, playhead_beat, generation,
                            spec[7],
                        ): spec[0]
                        for spec in parallel_specs
                    }
                    for fut, trk in list(futures.items()):
                        try:
                            fut.result()
                        except Exception as exc:
                            with self._track_cache_lock:
                                self._track_audio_caches.pop(trk.id, None)
                            print(f"[AudioEngine] Erreur cache {trk.name}: {exc}")
                        completed_count += 1
                        self._report_caching_progress(
                            completed_count, total_jobs, trk.name, min(0.999, completed_count / total_jobs)
                        )

            with self._track_cache_lock:
                self._cache_done_cond.notify_all()
            self._report_caching_progress(total_jobs, max(1, total_jobs), "Prêt", 1.0)

    def _read_cache_entry_slice(
        self,
        cache: Dict[str, Any],
        start_b: float,
        frames: int,
        bpm: float,
        out_buf: Optional[np.ndarray],
    ) -> Optional[np.ndarray]:
        """Lit `[s_start, s_end)` depuis une entrée de cache validée dans `out_buf` (0 alloc) ou une copie."""
        buf = cache.get("audio")
        if buf is None or len(buf) == 0:
            return None
        cache_sr = cache.get("sample_rate", self.sample_rate)
        if cache_sr != self.sample_rate or cache.get("bpm", bpm) != bpm:
            return None

        valid_start = int(cache.get("valid_start_frame", 0))
        fully_rendered = bool(cache.get("fully_rendered", False))
        ready_frames = int(cache.get("rendered_frames", len(buf) if fully_rendered else 0))

        beats_per_sec = bpm / 60.0
        s_start = int((start_b / beats_per_sec) * self.sample_rate)
        s_end = s_start + frames

        dest = out_buf if (out_buf is not None and out_buf.shape == (frames, 2)) else None

        if self.project and self.project.loop_enabled and self.project.loop_end_beat > self.project.loop_start_beat:
            loop_s_start = int((self.project.loop_start_beat / beats_per_sec) * self.sample_rate)
            loop_s_end = int((self.project.loop_end_beat / beats_per_sec) * self.sample_rate)
            loop_len = max(1, loop_s_end - loop_s_start)

            if s_start >= loop_s_end:
                s_start = loop_s_start + ((s_start - loop_s_start) % loop_len)
                s_end = s_start + frames

            if s_start < valid_start:
                return None

            if s_end <= loop_s_end and s_end <= ready_frames:
                if dest is not None:
                    np.copyto(dest, buf[s_start:s_end])
                    return dest
                return buf[s_start:s_end].copy()
            else:
                if s_start < loop_s_end and loop_s_end > ready_frames:
                    return None
                rem_frames = s_end - loop_s_end
                if rem_frames > 0 and (loop_s_start < valid_start or loop_s_start + rem_frames > ready_frames):
                    return None
                boundary = min(loop_s_end, len(buf))
                p1 = max(0, boundary - s_start)
                if dest is None:
                    dest = np.zeros((frames, 2), dtype=np.float32)
                else:
                    dest.fill(0.0)
                if p1 > 0:
                    dest[:p1] = buf[s_start:boundary]
                rem = frames - p1
                if rem > 0 and loop_s_start + rem <= len(buf):
                    dest[p1:] = buf[loop_s_start:loop_s_start + rem]
                return dest
        else:
            if s_start < valid_start:
                return None
            if s_end <= ready_frames and s_end <= len(buf):
                if dest is not None:
                    np.copyto(dest, buf[s_start:s_end])
                    return dest
                return buf[s_start:s_end].copy()
            elif not fully_rendered or valid_start > 0 or ready_frames < len(buf):
                return None
            elif s_start < len(buf):
                if dest is None:
                    dest = np.zeros((frames, 2), dtype=np.float32)
                else:
                    dest.fill(0.0)
                p1 = len(buf) - s_start
                dest[:p1] = buf[s_start:]
                return dest
            else:
                if dest is not None:
                    dest.fill(0.0)
                    return dest
                return np.zeros((frames, 2), dtype=np.float32)

    def _get_cached_track_slice_ex(
        self,
        track: Track,
        start_b: float,
        end_b: float,
        frames: int,
        bpm: float,
        out_buf: Optional[np.ndarray] = None,
        verify_inst_sig: bool = True,
        prefer_dry: bool = False,
    ) -> tuple[Optional[np.ndarray], bool]:
        """Récupère la tranche ASIO-Guard (post-FX ou dry) en O(1) et indique si les inserts sont déjà inclus."""
        tid = track.id
        with self._track_cache_lock:
            cur_tgen = self._track_generations.get(tid, 0)
            cur_fgen = self._track_fx_generations.get(tid, 0)
            cache = None if prefer_dry else self._track_audio_caches.get(tid)
            dry_cache = self._track_dry_caches.get(tid)

            if cache is not None and cache.get("is_valid", False) and cache.get("audio") is not None:
                if "track_gen" in cache and cache.get("track_gen") != cur_tgen:
                    cache = None
                elif "fx_gen" in cache and cache.get("fx_gen") != cur_fgen:
                    cache = None
                elif verify_inst_sig and "inst_sig" in cache:
                    cur_sig = self._fast_inst_signature(track)
                    if cache.get("inst_sig") != cur_sig:
                        self._track_audio_caches.pop(tid, None)
                        self._track_dry_caches.pop(tid, None)
                        self._track_generations[tid] = cur_tgen + 1
                        self._cache_generation += 1
                        self._state_generation += 1
                        self._schedule_debounce_caching()
                        return None, False
            else:
                cache = None

            if cache is None and dry_cache is not None and dry_cache.get("is_valid", False) and dry_cache.get("audio") is not None:
                if "track_gen" in dry_cache and dry_cache.get("track_gen") != cur_tgen:
                    dry_cache = None
                elif verify_inst_sig and "inst_sig" in dry_cache:
                    cur_sig = self._fast_inst_signature(track)
                    if dry_cache.get("inst_sig") != cur_sig:
                        self._track_dry_caches.pop(tid, None)
                        self._track_generations[tid] = cur_tgen + 1
                        self._cache_generation += 1
                        self._state_generation += 1
                        self._schedule_debounce_caching()
                        return None, False
            else:
                if cache is not None:
                    dry_cache = None

        if cache is not None:
            res = self._read_cache_entry_slice(cache, start_b, frames, bpm, out_buf)
            if res is not None:
                return res, bool(cache.get("includes_inserts", False))

        if dry_cache is not None:
            res = self._read_cache_entry_slice(dry_cache, start_b, frames, bpm, out_buf)
            if res is not None:
                return res, False

        return None, False

    def _get_cached_track_slice(
        self,
        track: Track,
        start_b: float,
        end_b: float,
        frames: int,
        bpm: float,
        out_buf: Optional[np.ndarray] = None,
        verify_inst_sig: bool = True,
    ) -> Optional[np.ndarray]:
        """Récupère instantanément la tranche audio pré-calculée en RAM (ASIO-Guard)."""
        res, _ = self._get_cached_track_slice_ex(
            track, start_b, end_b, frames, bpm, out_buf=out_buf, verify_inst_sig=verify_inst_sig
        )
        return res

    def _can_auto_bypass_idle_track(self, track: Track, native_inst: Optional[Any] = None) -> bool:
        """Retourne True si tous les effets de la piste sont des plugins NovaDAW standards sans VST externe."""
        if getattr(track, "insert_effects", None) and any(track.insert_effects):
            return False
        for p in getattr(track, "plugins", []) or []:
            if self._is_non_insert_plugin(p, track, native_inst) or not getattr(p, "enabled", True):
                continue
            ptype = str(getattr(p, "plugin_type_id", "") or "")
            if not ptype.startswith("novadaw."):
                return False
        return True

    def _track_has_overlapping_content(
        self,
        track: Track,
        start_b: float,
        end_b: float,
        beats_per_sec: float,
        native_inst: Optional[Any] = None,
    ) -> bool:
        """Vérifie en O(clips) si un clip (ou sa queue de résonance) chevauche `[start_b, end_b)`."""
        clips = getattr(track, "clips", None)
        if not clips:
            if native_inst is not None and bool(getattr(native_inst, "_active_voices", None)):
                return True
            return False

        is_drum = (
            (native_inst is not None and getattr(native_inst, "plugin_type_id", None) == "novadaw.drum_machine")
            or getattr(track, "plugin_path", None) == "novadaw.drum_machine"
        )
        if is_drum and native_inst is not None and bool(getattr(native_inst, "_active_voices", None)):
            return True

        if track.track_type == "midi":
            if is_drum:
                tail_beats = max(4.0, 4.0 * max(0.2, beats_per_sec))
            elif native_inst is not None:
                tail_beats = self._compute_native_release_tail_beats(native_inst, False, beats_per_sec, 2.0)
            else:
                tail_beats = 6.0
        else:
            tail_beats = 0.25

        for clip in clips:
            c_start = float(clip.start_beat)
            if c_start > end_b:
                continue
            c_end = c_start + float(clip.length_beats)
            if isinstance(clip, MidiClip):
                if not clip.notes:
                    continue
                if c_end + tail_beats >= start_b:
                    return True
                # Une note MIDI peut dépasser la fin nominale du clip
                for n in clip.notes:
                    if (c_start + n.start_beat) < end_b and (c_start + n.start_beat + n.duration + tail_beats) >= start_b:
                        return True
            elif isinstance(clip, AudioClip):
                if getattr(clip, "audio_data", None) is not None and c_end + tail_beats >= start_b:
                    return True
        return False

    def _render_track_generator_slice(
        self,
        track: Track,
        start_b: float,
        end_b: float,
        frames: int,
        bpm: float,
        out_buf: Optional[np.ndarray] = None,
    ) -> tuple[np.ndarray, Optional[Any]]:
        if out_buf is not None and out_buf.shape == (frames, 2):
            out_buf.fill(0.0)
            track_buf = out_buf
        else:
            track_buf = np.zeros((frames, 2), dtype=np.float32)
        beats_per_sec = bpm / 60.0
        native_inst = None

        if track.track_type == "midi":
            # 0. Si la piste est routée vers un instrument hébergé sur une autre piste (synth_route_track_id)
            target_track = None
            if getattr(track, "synth_route_track_id", None) and self.project:
                target_track = self.project.get_track(track.synth_route_track_id)

            # 1. Vérifier si la piste est routée vers un instrument virtuel natif (ex: Nova Drums VSTi, NovaSynth)
            native_inst = self.get_native_instrument(target_track) if target_track else self.get_native_instrument(track)
            rendered_by_native = False

            if native_inst and hasattr(native_inst, "render_slice"):
                if not getattr(native_inst, "enabled", True):
                    rendered_by_native = True
                else:
                    clip_notes = []
                    # Marge de queue de release ADSR adaptée (0 pour batterie one-shot, 1.5 pour synthé)
                    is_drum = (getattr(native_inst, "plugin_type_id", None) == "novadaw.drum_machine" or
                               getattr(track, "plugin_path", None) == "novadaw.drum_machine")
                    release_tail_beats = self._compute_native_release_tail_beats(native_inst, is_drum, beats_per_sec, 1.5)
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
                    channel = self._midi_status(0, track)
                    previous_channel = self._vst_midi_channels.get(track.id, channel)
                    if previous_channel != channel:
                        # Release the previous part before sending notes to the new one.
                        midi_messages.append((bytes([0xB0 | previous_channel, 123, 0]), 0.0))
                    self._vst_midi_channels[track.id] = channel
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
                                midi_messages.append((bytes([self._midi_status(0x90, track), note.pitch, max(1, min(127, note.velocity))]), offset))
                                has_notes = True

                            # Détection Note-Off dans cette tranche
                            if start_b <= abs_note_end < end_b:
                                offset = max(0.0, min(dur_sec, (abs_note_end - start_b) / beats_per_sec))
                                midi_messages.append((bytes([self._midi_status(0x80, track), note.pitch, 0]), offset))
                            elif abs_note_start < end_b and abs_note_end > start_b:
                                has_notes = True

                    with self._live_midi_lock:
                        live_evs = self._live_midi_queue.pop(track.id, None)
                        if live_evs:
                            midi_messages.extend(live_evs)
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

                            # Offset dans le buffer (round évite la répétition d'1 échantillon par dérive flottante)
                            note_offset_samples = round((abs_note_start - start_b) / beats_per_sec * self.sample_rate)
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
            # Beat conversion can produce 4095.999999999 instead of frame 4096.
            # Rounding avoids repeating one sample at offline block boundaries.
            sample_offset = round(((start_b - clip_start + offset_beats) / beats_per_sec) * self.sample_rate)

            buf_start = 0
            clip_read_start = sample_offset
            if clip_read_start < 0:
                buf_start = -clip_read_start
                clip_read_start = 0

            max_clip_samples = round(((offset_beats + clip.length_beats) / beats_per_sec) * self.sample_rate)
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

        return track_buf, native_inst

    def _is_track_live_active(self, track_id: str) -> bool:
        """Indique si la piste reçoit des notes live en cours (zéro verrou ni time.time() au repos)."""
        if (
            self._live_active_track is None
            and not self._live_midi_queue
            and not self._live_active_notes
            and not self._live_native_notes
            and not self._live_native_tail_until
        ):
            return False
        if self._live_active_track is not None and getattr(self._live_active_track, "id", None) == track_id:
            if time.time() < self._live_tail_until:
                return True
        if track_id in self._live_midi_queue and self._live_midi_queue[track_id]:
            return True
        for tid, _p in self._live_active_notes:
            if tid == track_id:
                return True
        if self._live_native_notes or self._live_native_tail_until:
            with self._live_native_lock:
                if track_id in self._live_native_tail_until and time.time() < self._live_native_tail_until[track_id]:
                    return True
                for n in self._live_native_notes:
                    if n["track_id"] == track_id:
                        return True
        return False

    def _process_track_effects_chain(
        self,
        track: Track,
        track_buf: np.ndarray,
        start_b: float,
        end_b: float,
        frames: int,
        bpm: float,
        native_inst: Any = None,
    ) -> np.ndarray:
        """Applique les VST et la pile de plugins natifs d'une piste sans réallouer AutomationProcessContext."""
        if getattr(track, "insert_effects", None):
            track_buf = self._process_vst_effects(track, track_buf)
        plugins = getattr(track, "plugins", None)
        if plugins and track_buf is not None:
            ctx = None
            for plugin in plugins:
                if self._is_non_insert_plugin(plugin, track, native_inst):
                    continue
                if not getattr(plugin, "enabled", True):
                    continue
                try:
                    pwc = getattr(type(plugin), "process_with_context", None)
                    needs_ctx = pwc is not None and pwc is not BasePlugin.process_with_context
                    if needs_ctx:
                        if ctx is None:
                            tid = track.id
                            ctx = self._cb_auto_ctx_cache.get(tid)
                            if ctx is None:
                                ctx = AutomationProcessContext(
                                    track_id=tid,
                                    start_beat=start_b,
                                    end_beat=end_b,
                                    bpm=bpm,
                                    sample_rate=self.sample_rate,
                                    is_playing=self.is_playing,
                                    automation_sink=self._enqueue_plugin_automation_write,
                                    read_automation=lambda param, beat, _t=track: self._read_track_automation_value(_t, param, beat),
                                )
                                self._cb_auto_ctx_cache[tid] = ctx
                            else:
                                ctx.start_beat = start_b
                                ctx.end_beat = end_b
                                ctx.bpm = bpm
                                ctx.sample_rate = self.sample_rate
                                ctx.is_playing = self.is_playing
                                ctx.read_automation = lambda param, beat, _t=track: self._read_track_automation_value(_t, param, beat)
                        res = plugin.process_with_context(track_buf, self.sample_rate, ctx)
                    else:
                        res = plugin.process(track_buf, self.sample_rate)
                    track_buf = self._normalize_plugin_output(res, track_buf, frames)
                except Exception:
                    pass
        return track_buf

    def _render_track_slice(
        self,
        track: Track,
        start_b: float,
        end_b: float,
        frames: int,
        bpm: float,
        live_input_buf: Optional[np.ndarray] = None,
        update_mixer_peaks: bool = True,
        out_buf: Optional[np.ndarray] = None,
        verify_inst_sig: bool = True,
        allow_none_on_silence: bool = False,
    ) -> Optional[np.ndarray]:
        tid = track.id
        track_buf = None
        includes_inserts = False
        native_inst = None

        live_step = self._playback_live_step
        has_live_playback_notes = bool(live_step is not None and tid in live_step[2])
        is_live_or_monitored = (
            getattr(track, "armed", False)
            or getattr(track, "monitoring", False)
            or live_input_buf is not None
            or has_live_playback_notes
            or self._is_track_live_active(tid)
        )

        # 0. ASIO-Guard Cache Hit (avec inserts statiques déjà intégrés ou repli dry)
        if self._asio_guard_enabled and not getattr(track, "armed", False) and not getattr(track, "monitoring", False) and live_input_buf is None and not self._is_track_live_active(tid):
            track_buf, includes_inserts = self._get_cached_track_slice_ex(
                track, start_b, end_b, frames, bpm,
                out_buf=out_buf, verify_inst_sig=verify_inst_sig, prefer_dry=has_live_playback_notes,
            )
            if track_buf is not None and includes_inserts and not has_live_playback_notes:
                if allow_none_on_silence and not update_mixer_peaks and frames > 0:
                    if self._peak_abs_1d(track_buf[:, 0], frames) == 0.0 and self._peak_abs_1d(track_buf[:, 1], frames) == 0.0:
                        return None
                if update_mixer_peaks:
                    mixer_plugin = self._get_active_mixer_plugin()
                    if mixer_plugin and frames > 0:
                        left_gain, right_gain, _, _ = self._compute_track_automation_gains(track, start_b, end_b, frames)
                        if isinstance(left_gain, np.ndarray):
                            pk_l = float(np.max(np.abs(track_buf[:, 0] * left_gain)))
                            pk_r = float(np.max(np.abs(track_buf[:, 1] * right_gain)))
                        else:
                            pk_l = self._peak_abs_1d(track_buf[:, 0], frames) * float(left_gain)
                            pk_r = self._peak_abs_1d(track_buf[:, 1], frames) * float(right_gain)
                        dt_sec = frames / float(max(8000, int(self.sample_rate)))
                        mixer_plugin.update_track_peak(tid, pk_l, pk_r, dt_sec=dt_sec)
                return track_buf

        if track.track_type == "midi":
            target_track = None
            if getattr(track, "synth_route_track_id", None) and self.project:
                target_track = self.project.get_track(track.synth_route_track_id)
            native_inst = self.get_native_instrument(target_track) if target_track else self.get_native_instrument(track)

        idle_st = self._track_idle_state.get(tid)
        can_bypass_idle = self._can_auto_bypass_idle_track(track, native_inst)

        # 0.bis Court-circuit ultra-rapide des pistes inactives / sans clip / après la fin du morceau (-100 dB)
        if (
            track_buf is None
            and not is_live_or_monitored
            and can_bypass_idle
            and not (idle_st is not None and idle_st.get("active", False))
            and not self._track_has_overlapping_content(track, start_b, end_b, bpm / 60.0, native_inst)
        ):
            if update_mixer_peaks:
                mixer_plugin = self._get_active_mixer_plugin()
                if mixer_plugin:
                    dt_sec = frames / float(max(8000, int(self.sample_rate)))
                    mixer_plugin.update_track_peak(tid, 0.0, 0.0, dt_sec=dt_sec)
            if allow_none_on_silence:
                return None
            if out_buf is not None and out_buf.shape == (frames, 2):
                out_buf.fill(0.0)
                return out_buf
            return np.zeros((frames, 2), dtype=np.float32)

        if track_buf is None:
            track_buf, rendered_inst = self._render_track_generator_slice(
                track, start_b, end_b, frames, bpm, out_buf=out_buf
            )
            if native_inst is None:
                native_inst = rendered_inst

        if track_buf is None:
            if out_buf is not None and out_buf.shape == (frames, 2):
                out_buf.fill(0.0)
                track_buf = out_buf
            else:
                track_buf = np.zeros((frames, 2), dtype=np.float32)

        if live_input_buf is not None and len(live_input_buf) > 0:
            n_copy = min(len(track_buf), len(live_input_buf))
            track_buf[:n_copy] += live_input_buf[:n_copy]

        # 3.bis Notes live natives jouées pendant la lecture (mixées avant l'ampli / chaîne FX)
        if has_live_playback_notes and live_step is not None:
            l_start_b, l_end_b, l_grouped = live_step
            live_wave, live_inst = self._render_track_live_native_audio(
                tid, track, l_grouped[tid], l_start_b, l_end_b, frames, use_isolated_ctx=True
            )
            if live_wave is not None and len(live_wave) > 0:
                n_cp = min(len(track_buf), len(live_wave))
                track_buf[:n_cp] += live_wave[:n_cp]
            if native_inst is None:
                native_inst = live_inst

        # 4. Auto-bypass d'inserts à -100 dB (évite le biais DC des triodes d'ampli et le coût CPU au silence)
        gen_pk = (
            max(self._peak_abs_1d(track_buf[:, 0], frames), self._peak_abs_1d(track_buf[:, 1], frames))
            if frames > 0
            else 0.0
        )
        if idle_st is None:
            idle_st = {"active": False, "silent_frames": 0}
            self._track_idle_state[tid] = idle_st

        if gen_pk <= 1e-5 and not idle_st["active"] and can_bypass_idle and not is_live_or_monitored:
            if update_mixer_peaks:
                mixer_plugin = self._get_active_mixer_plugin()
                if mixer_plugin:
                    dt_sec = frames / float(max(8000, int(self.sample_rate)))
                    mixer_plugin.update_track_peak(tid, 0.0, 0.0, dt_sec=dt_sec)
            if allow_none_on_silence:
                return None
            track_buf.fill(0.0)
            return track_buf

        if gen_pk > 1e-5 or is_live_or_monitored:
            idle_st["active"] = True
            idle_st["silent_frames"] = 0

        track_buf = self._process_track_effects_chain(track, track_buf, start_b, end_b, frames, bpm, native_inst=native_inst)

        if gen_pk <= 1e-5 and can_bypass_idle and not is_live_or_monitored and frames > 0:
            fx_pk = max(self._peak_abs_1d(track_buf[:, 0], frames), self._peak_abs_1d(track_buf[:, 1], frames))
            if fx_pk <= 1e-5:
                idle_st["silent_frames"] += frames
                if idle_st["silent_frames"] >= max(frames, int(0.020 * self.sample_rate)):
                    idle_st["active"] = False
                    idle_st["silent_frames"] = 0
                    self._reset_track_insert_fx(track)
                    track_buf.fill(0.0)
            else:
                idle_st["silent_frames"] = 0

        # 5. Envoi des niveaux de crête au plugin Mixeur pour les VU-mètres (post-fader) lors d'appels autonomes
        if update_mixer_peaks:
            mixer_plugin = self._get_active_mixer_plugin()
            if mixer_plugin and track_buf is not None and frames > 0:
                left_gain, right_gain, _, _ = self._compute_track_automation_gains(track, start_b, end_b, frames)
                if isinstance(left_gain, np.ndarray):
                    pk_l = float(np.max(np.abs(track_buf[:, 0] * left_gain)))
                    pk_r = float(np.max(np.abs(track_buf[:, 1] * right_gain)))
                else:
                    pk_l = self._peak_abs_1d(track_buf[:, 0], frames) * float(left_gain)
                    pk_r = self._peak_abs_1d(track_buf[:, 1], frames) * float(right_gain)
                dt_sec = frames / float(max(8000, int(self.sample_rate)))
                mixer_plugin.update_track_peak(tid, pk_l, pk_r, dt_sec=dt_sec)

        return track_buf

    def _enqueue_plugin_automation_write(
        self,
        track_id: str,
        target_param: str,
        beat: float,
        value: float,
        plugin_id: str = "",
    ) -> None:
        """Collecte sans blocage les points d'automatisation émis par un plugin DSP."""
        with self._automation_write_lock:
            if len(self._pending_automation_writes) < 4096:
                self._pending_automation_writes.append(
                    (track_id, target_param, float(beat), float(value), plugin_id)
                )

    def drain_pending_automation_writes(self) -> List[tuple[str, str, float, float, str]]:
        """Vide et retourne les points d'automatisation émis par les plugins pour le thread UI."""
        with self._automation_write_lock:
            items = self._pending_automation_writes
            self._pending_automation_writes = []
            return items

    def _read_track_automation_value(self, track: Track, target_param: str, beat: float) -> Optional[float]:
        """Lit la valeur automatisée d'un paramètre pour un plugin consultant le contexte."""
        lane = track.get_automation_lane(target_param) if hasattr(track, "get_automation_lane") else None
        if lane and lane.enabled and lane.points:
            return lane.evaluate(beat)
        if target_param == "volume":
            return float(track.volume)
        if target_param == "pan":
            return float(track.pan)
        return None

    def _compute_track_automation_gains(
        self,
        track: Track,
        start_beat: float,
        end_beat: float,
        frames: int,
    ) -> tuple[Any, Any, float, float]:
        """
        Calcule les gains gauche/droite (scalaires ou vecteurs NumPy par échantillon)
        en tenant compte des pistes d'automatisation Volume et Panoramique actives.
        """
        lanes = getattr(track, "automation_lanes", None) or []
        vol_lane = None
        pan_lane = None
        for lane in lanes:
            if not getattr(lane, "enabled", True) or not getattr(lane, "points", None):
                continue
            if getattr(lane, "target_scope", "track") != "track":
                continue
            if lane.target_param == "volume" and vol_lane is None:
                vol_lane = lane
            elif lane.target_param == "pan" and pan_lane is None:
                pan_lane = lane

        if vol_lane is None and pan_lane is None:
            vol = float(getattr(track, "volume", 1.0))
            pan = max(-1.0, min(1.0, float(getattr(track, "pan", 0.0))))
            return vol * (1.0 - max(0.0, pan)), vol * (1.0 + min(0.0, pan)), vol, pan

        if vol_lane is not None:
            vol_arr = np.clip(
                vol_lane.evaluate_range(start_beat, end_beat, frames, fallback_value=track.volume),
                0.0,
                1.5,
            )
            eff_vol = float(vol_arr[-1]) if len(vol_arr) > 0 else float(track.volume)
        else:
            vol_arr = float(getattr(track, "volume", 1.0))
            eff_vol = vol_arr

        if pan_lane is not None:
            pan_arr = np.clip(
                pan_lane.evaluate_range(start_beat, end_beat, frames, fallback_value=track.pan),
                -1.0,
                1.0,
            )
            eff_pan = float(pan_arr[-1]) if len(pan_arr) > 0 else float(track.pan)
        else:
            pan_arr = max(-1.0, min(1.0, float(getattr(track, "pan", 0.0))))
            eff_pan = pan_arr

        left_gain = vol_arr * (1.0 - np.maximum(0.0, pan_arr))
        right_gain = vol_arr * (1.0 + np.minimum(0.0, pan_arr))
        return left_gain, right_gain, eff_vol, eff_pan

    def get_track_live_automation_values(self, track_id: str) -> Optional[Dict[str, float]]:
        """Retourne les dernières valeurs Volume/Pan évaluées pendant la lecture."""
        return self._live_automation_values.get(track_id)

    def export_wav(self, file_path: str, end_bar: int = 8) -> bool:
        """Render from fresh native DSP state, never from the live playback cache."""
        if not self.project:
            return False
        with self._export_lock:
            renderer = copy.copy(self)
            project = copy.copy(self.project)

            def clone_track(track):
                clone = Track.from_dict(copy.deepcopy(track.to_dict()))
                for source, dest in zip(track.clips, clone.clips):
                    if isinstance(source, AudioClip):
                        dest.audio_data = source.audio_data
                for plugin in clone.plugins:
                    if hasattr(plugin, "set_project"):
                        plugin.set_project(project)
                return clone

            project.tracks = [clone_track(track) for track in self.project.tracks]
            project.master_track = clone_track(self.project.master_track) if self.project.master_track else None
            # An export is a linear timeline, including any requested release tail.
            project.loop_enabled = False
            project._rack_native_plugins = {}
            renderer.project = project
            renderer._asio_guard_enabled = False
            renderer._track_audio_caches = {}
            renderer._track_dry_caches = {}
            renderer._track_idle_state = {}
            renderer._isolated_fx_clones = {}
            renderer._cb_track_bufs = {}
            renderer._cb_mono_scratch = None
            renderer._cb_peak_slots = {}
            renderer._cb_active_peak_tids = []
            renderer._cb_auto_ctx_cache = {}
            renderer._live_automation_values = {}
            renderer._live_native_contexts = {}
            renderer.track_plugins = {}
            renderer._vst_midi_channels = {}
            renderer._master_peak_lock = threading.Lock()
            renderer._track_cache_lock = threading.RLock()
            renderer._cache_done_cond = threading.Condition(renderer._track_cache_lock)
            renderer.is_playing = False
            renderer._reset_all_plugins()
            return renderer._render_mixdown_wav(file_path, end_bar)

    def _render_mixdown_wav(self, file_path: str, end_bar: int) -> bool:
        """Offline mixdown, called only on the isolated rendering context."""
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
                    left_gain, right_gain, _, _ = self._compute_track_automation_gains(track, start_b, end_b, frames)
                    sig[:, 0] *= left_gain
                    sig[:, 1] *= right_gain
                    out_chunk += sig

            # Traitement Master sur le mixage global
            has_active_mixer = False
            if self.project and getattr(self.project, "master_track", None):
                master_t = self.project.master_track
                has_active_mixer = any(getattr(p, "plugin_type_id", "") == "novadaw.mixer" and getattr(p, "enabled", True) for p in getattr(master_t, "plugins", []))
                if not master_t.muted:
                    if not has_active_mixer:
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

        if not has_active_mixer:
            rendered *= self.master_volume
        rendered = np.tanh(rendered)
        sf.write(file_path, rendered, self.sample_rate, subtype="PCM_24")
        return True

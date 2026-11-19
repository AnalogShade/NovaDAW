"""
tests/test_guitar_and_amp_plugins.py - Suite de tests complète pour Nova Guitar et Nova Guitar Amp.

Vérifie :
- Enregistrement dans PluginRegistry (catégories instrument et effect)
- Justesse d'accordage du Digital Waveguide (FFT sur Mi2, La2, Mi4)
- Effet physique de la tension/rigidité, position d'attaque (pluck) et palm mute
- Continuité temps réel bloc par bloc (render_slice et GuitarAmpDSP) sans clics ni NaN
- Tone Stack (Low / Mid / Treble), Distorsion oversamplée 4x et Cab Sim
- Bypass transparent sur les deux plugins
- Gestion des presets usine et utilisateur via PresetManager + sérialisation .ndaw
- Rendu complet dans AudioEngine (Nova Guitar -> Nova Guitar Amp) et création des GUI
"""
import os
import sys
import math
import numpy as np
import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.project import Project, Track, MidiClip, MidiNote
from core.serializer import save_project, load_project
from core.preset_manager import PresetManager
from core.audio_engine import AudioEngine
from plugins.registry import plugin_registry, ensure_plugins_loaded
from plugins.guitar.guitar_plugin import NovaGuitarPlugin
from plugins.guitar_amp.amp_plugin import NovaGuitarAmpPlugin


@pytest.fixture(scope="module", autouse=True)
def _load_plugins():
    ensure_plugins_loaded()


def _dominant_freq(mono: np.ndarray, sr: int) -> float:
    """Estime la fréquence fondamentale par FFT (interpolation parabolique)."""
    win = np.hanning(len(mono))
    spec = np.abs(np.fft.rfft(mono * win))
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sr)
    # Ignorer le très bas DC (< 30 Hz)
    min_bin = int(30.0 * len(mono) / sr)
    peak_idx = min_bin + int(np.argmax(spec[min_bin:]))
    if 1 <= peak_idx < len(spec) - 1:
        a, b, c = spec[peak_idx - 1], spec[peak_idx], spec[peak_idx + 1]
        denom = (a - 2.0 * b + c)
        if abs(denom) > 1e-12:
            delta = 0.5 * (a - c) / denom
            return float(freqs[peak_idx] + delta * (sr / len(mono)))
    return float(freqs[peak_idx])


def _spectral_centroid(mono: np.ndarray, sr: int) -> float:
    win = np.hanning(len(mono))
    mag = np.abs(np.fft.rfft(mono * win))
    freqs = np.fft.rfftfreq(len(mono), 1.0 / sr)
    s = float(np.sum(mag))
    return float(np.sum(freqs * mag) / s) if s > 1e-9 else 0.0


def test_registration_and_metadata():
    g_info = plugin_registry.get_plugin_info("novadaw.guitar")
    a_info = plugin_registry.get_plugin_info("novadaw.guitar_amp")
    assert g_info is not None and g_info["category"] == "instrument"
    assert a_info is not None and a_info["category"] == "effect"

    guitar = plugin_registry.create_plugin("novadaw.guitar")
    amp = plugin_registry.create_plugin("novadaw.guitar_amp")
    assert isinstance(guitar, NovaGuitarPlugin)
    assert isinstance(amp, NovaGuitarAmpPlugin)
    assert guitar.is_instrument is True


@pytest.mark.parametrize(
    "midi_pitch,expected_hz",
    [
        (40, 82.407),   # E2 (Mi grave)
        (45, 110.000),  # A2 (La)
        (64, 329.628),  # E4 (Mi aigu)
    ],
)
def test_guitar_waveguide_tuning_accuracy(midi_pitch, expected_hz):
    guitar = NovaGuitarPlugin()
    guitar.apply_preset("Électrique Strat Clean (Manche)")
    guitar.reverb_mix = 0.0
    sr = 44100
    wave = guitar.render_note(midi_pitch, duration_sec=1.0, sample_rate=sr, velocity=100)
    assert wave.ndim == 2 and wave.shape[1] == 2
    assert wave.dtype == np.float32
    assert np.all(np.isfinite(wave))
    assert float(np.max(np.abs(wave))) > 0.05

    # Analyser après l'attaque (0.15s..0.75s) pour mesurer la hauteur soutenue
    seg = wave[int(0.15 * sr):int(0.75 * sr), 0]
    est_hz = _dominant_freq(seg, sr)
    # Vérifier la fondamentale ou sa tolérance à +/- 1.5 % (dans le cas où l'octave 2 domine sur micro manche, vérifier modulo octave)
    cents_err = 1200.0 * math.log2(max(1.0, est_hz) / expected_hz)
    assert abs(cents_err) < 20.0 or abs(cents_err - 1200.0) < 20.0, f"Accordage hors tolérance : {est_hz:.2f} Hz vs {expected_hz:.2f} Hz"


def test_guitar_physical_parameters_affect_timbre():
    sr = 44100
    # 1. Position d'attaque : près du chevalet (0.08) plus brillante que vers le manche (0.42)
    g_bridge = NovaGuitarPlugin()
    g_bridge.apply_preset("Électrique Strat Clean (Manche)")
    g_bridge.pluck_position = 0.08
    w_bridge = g_bridge.render_note(52, duration_sec=0.5, sample_rate=sr, velocity=110)

    g_neck = NovaGuitarPlugin()
    g_neck.apply_preset("Électrique Strat Clean (Manche)")
    g_neck.pluck_position = 0.42
    w_neck = g_neck.render_note(52, duration_sec=0.5, sample_rate=sr, velocity=110)

    c_bridge = _spectral_centroid(w_bridge[:int(0.25 * sr), 0], sr)
    c_neck = _spectral_centroid(w_neck[:int(0.25 * sr), 0], sr)
    assert c_bridge > c_neck, f"Pluck chevalet ({c_bridge:.1f} Hz) devrait être plus brillant que manche ({c_neck:.1f} Hz)"

    # 2. Palm mute réduit fortement l'énergie de sustain
    g_open = NovaGuitarPlugin()
    g_open.apply_preset("Électrique Tele Twang (Chevalet)")
    g_open.palm_mute = 0.0
    w_open = g_open.render_note(40, duration_sec=0.8, sample_rate=sr, velocity=110)

    g_muted = NovaGuitarPlugin()
    g_muted.apply_preset("Électrique Tele Twang (Chevalet)")
    g_muted.palm_mute = 0.85
    w_muted = g_muted.render_note(40, duration_sec=0.8, sample_rate=sr, velocity=110)

    rms_open = float(np.sqrt(np.mean(w_open[int(0.35 * sr):int(0.75 * sr)] ** 2)))
    rms_muted = float(np.sqrt(np.mean(w_muted[int(0.35 * sr):int(0.75 * sr)] ** 2)))
    assert rms_muted < rms_open * 0.45

    # 3. Tension de corde modifie le spectre sans casser la stabilité
    g_low_t = NovaGuitarPlugin()
    g_low_t.string_tension = 0.15
    w_low_t = g_low_t.render_note(45, duration_sec=0.6, sample_rate=sr, velocity=100)
    g_high_t = NovaGuitarPlugin()
    g_high_t.string_tension = 0.95
    w_high_t = g_high_t.render_note(45, duration_sec=0.6, sample_rate=sr, velocity=100)
    assert not np.allclose(w_low_t, w_high_t)
    assert np.all(np.isfinite(w_low_t)) and np.all(np.isfinite(w_high_t))


def test_guitar_release_note_off_across_paths(monkeypatch):
    sr = 44100
    # 1. render_note sur Mi2 (40) et La3 (57) : 0.04 s < 0.24 s < 1.50 s et pas de clic au Note-Off
    for pitch in (40, 57):
        waves = {}
        for rel in (0.04, 0.24, 1.50):
            g = NovaGuitarPlugin()
            g.apply_preset("Basse Électrique Clean (Pick)")
            g.reverb_mix = 0.0
            g.sustain_sec = 10.0
            g.release_sec = rel
            waves[rel] = g.render_note(pitch, duration_sec=0.5, sample_rate=sr, velocity=105)

        assert len(waves[1.50]) > len(waves[0.24]) > len(waves[0.04])
        # Vérifier l'énergie juste après Note-Off (0.55s..0.75s) : 0.04 < 0.24 < 1.50
        rms_04 = float(np.sqrt(np.mean(waves[0.04][int(0.55 * sr):int(0.75 * sr)] ** 2))) if len(waves[0.04]) > int(0.75 * sr) else 0.0
        rms_24 = float(np.sqrt(np.mean(waves[0.24][int(0.55 * sr):int(0.75 * sr)] ** 2)))
        rms_150 = float(np.sqrt(np.mean(waves[1.50][int(0.55 * sr):int(0.75 * sr)] ** 2)))
        assert rms_04 < rms_24 * 0.45
        assert rms_24 < rms_150 * 0.60

        # À 1.50 s de release, la corde doit encore vibrer nettement 1.0 s après Note-Off (t=1.50s..1.90s)
        rms_late = float(np.sqrt(np.mean(waves[1.50][int(1.50 * sr):int(1.90 * sr)] ** 2)))
        assert rms_late > 0.002

        # Pas de discontinuité/clic au passage Note-Off (t=0.50s)
        noff = int(0.5 * sr)
        assert float(np.max(np.abs(np.diff(waves[0.04][noff - 256:noff + 256, 0])))) < 0.10

    # 2. Séquenceur / ASIO-Guard (_render_track_offline) ne tronque pas la queue à 1.50 s
    proj = Project.create_default()
    proj.bpm = 120.0
    trk = Track(name="G", track_type="midi", plugin_path="novadaw.guitar", plugin_name="Nova Guitar")
    g_seq = NovaGuitarPlugin()
    g_seq.apply_preset("Électrique Strat Clean (Manche)")
    g_seq.reverb_mix = 0.0
    g_seq.sustain_sec = 10.0
    g_seq.release_sec = 1.50
    trk.add_plugin(g_seq)
    clip = MidiClip(start_beat=0.0, length_beats=8.0)
    clip.notes.append(MidiNote(pitch=57, start_beat=0.0, duration=1.0, velocity=105))  # 0.5 s note-on
    trk.clips.append(clip)
    proj.tracks.append(trk)

    eng = AudioEngine()
    eng.project = proj
    rendered = eng._render_track_offline(trk, bpm=120.0, sample_rate=sr, end_beat=8.0)
    assert float(np.sqrt(np.mean(rendered[int(1.5 * sr):int(2.1 * sr)] ** 2))) > 0.001

    # 3. Clavier virtuel (start_live_note / stop_live_note) respecte release_sec
    monkeypatch.delenv("NOVADAW_SILENT_TESTS", raising=False)
    eng._stream = type("FakeStream", (), {"active": True})()
    lengths = {}
    for rel in (0.04, 1.50):
        g_seq.release_sec = rel
        eng._preview_buffers.clear()
        eng.start_live_note(57, velocity=100, track=trk)
        eng.stop_live_note(57, track=trk, min_gate_sec=0.0)
        lengths[rel] = len(eng._preview_buffers[-1]["buffer"])
    assert lengths[1.50] > lengths[0.04] * 10


def test_guitar_render_slice_continuity_and_bypass():
    guitar = NovaGuitarPlugin()
    guitar.apply_preset("Acoustique Folk Acier (Dreadnought)")
    guitar.reverb_mix = 0.0
    sr = 44100
    bpm = 120.0
    bps = bpm / 60.0
    notes = [
        MidiNote(pitch=40, start_beat=0.0, duration=2.0, velocity=100),
        MidiNote(pitch=47, start_beat=0.05, duration=2.0, velocity=95),
        MidiNote(pitch=52, start_beat=0.10, duration=2.0, velocity=95),
    ]

    # Rendu par petits blocs de 512 échantillons
    chunk_size = 512
    chunks = []
    for i in range(0, 44100, chunk_size):
        sb = (i / sr) * bps
        eb = ((i + chunk_size) / sr) * bps
        buf = guitar.render_slice(notes, sb, eb, bps, chunk_size, sr)
        assert buf.shape == (chunk_size, 2)
        assert np.all(np.isfinite(buf))
        chunks.append(buf)

    full = np.vstack(chunks)
    assert float(np.max(np.abs(full))) > 0.05

    # Vérifier qu'il n'y a pas de saut brutal aux frontières de blocs après l'attaque
    diffs = np.abs(np.diff(full[4096:, 0]))
    assert float(np.max(diffs)) < 0.25

    # Bypass doit rendre du silence propre
    guitar.enabled = False
    sil = guitar.render_slice(notes, 0.0, 0.5, bps, 1024, sr)
    assert float(np.max(np.abs(sil))) == 0.0


def test_guitar_amp_dsp_bypass_distortion_tonestack_cab():
    amp = NovaGuitarAmpPlugin()
    sr = 44100
    n = 4096
    t = np.arange(n, dtype=np.float32) / sr
    dry_mono = 0.35 * np.sin(2.0 * np.pi * 220.0 * t)
    dry = np.column_stack((dry_mono, dry_mono)).astype(np.float32)

    # 1. Bypass bit-identique
    amp.enabled = False
    out_byp = amp.process(dry, sr)
    assert np.allclose(out_byp, dry, atol=1e-7)

    # 2. Distorsion produit des harmoniques sans NaN ni dépassement excessif
    amp.enabled = True
    amp.apply_preset("High Gain Metal")
    amp.reset()
    # Traiter en 2 blocs consécutifs pour vérifier la continuité inter-buffers
    out1 = amp.process(dry[:2048], sr)
    out2 = amp.process(dry[2048:], sr)
    out_dist = np.vstack([out1, out2])
    assert out_dist.shape == dry.shape
    assert np.all(np.isfinite(out_dist))
    assert float(np.max(np.abs(out_dist))) <= 1.15

    # Présence d'harmoniques supérieures (3e harmonique 660 Hz)
    spec = np.abs(np.fft.rfft(out_dist[1024:, 0] * np.hanning(n - 1024)))
    freqs = np.fft.rfftfreq(n - 1024, 1.0 / sr)
    h3_bin = int(np.argmin(np.abs(freqs - 660.0)))
    f0_bin = int(np.argmin(np.abs(freqs - 220.0)))
    assert spec[h3_bin] > spec[f0_bin] * 0.10

    # 3. Tone Stack Low / Treble modifie bien le bilan spectral sur un bruit rose/blanc
    rng = np.random.default_rng(42)
    noise = (rng.uniform(-0.2, 0.2, size=(8192, 2))).astype(np.float32)

    amp_bass = NovaGuitarAmpPlugin(distortion=0.0, low_db=10.0, mid_db=0.0, treble_db=-10.0, cab_enabled=False)
    amp_treb = NovaGuitarAmpPlugin(distortion=0.0, low_db=-10.0, mid_db=0.0, treble_db=10.0, cab_enabled=False)
    out_bass = amp_bass.process(noise, sr)
    out_treb = amp_treb.process(noise, sr)
    assert _spectral_centroid(out_treb[1024:, 0], sr) > _spectral_centroid(out_bass[1024:, 0], sr)

    # 4. Cab Sim atténue les très hautes fréquences (> 6 kHz)
    amp_nocab = NovaGuitarAmpPlugin(distortion=0.6, cab_enabled=False)
    amp_cab = NovaGuitarAmpPlugin(distortion=0.6, cab_enabled=True)
    y_nocab = amp_nocab.process(noise, sr)
    y_cab = amp_cab.process(noise, sr)
    assert _spectral_centroid(y_cab[1024:, 0], sr) < _spectral_centroid(y_nocab[1024:, 0], sr)


def test_presets_and_project_serialization(tmp_path):
    pm = PresetManager(base_dir=tmp_path)

    guitar = NovaGuitarPlugin()
    assert pm.is_factory_preset(guitar, "Acoustique Nylon Classique")
    assert pm.load_preset(guitar, "Électrique Tele Twang (Chevalet)")
    assert guitar.guitar_mode == "electric_clean"

    guitar.string_tension = 0.41
    guitar.pluck_position = 0.17
    pm.save_preset(guitar, "Ma Strat Custom")
    assert pm.preset_exists("novadaw.guitar", "Ma Strat Custom")

    guitar2 = NovaGuitarPlugin()
    assert pm.load_preset(guitar2, "Ma Strat Custom")
    assert abs(guitar2.string_tension - 0.41) < 1e-4
    assert abs(guitar2.pluck_position - 0.17) < 1e-4

    amp = NovaGuitarAmpPlugin()
    amp.distortion = 0.77
    amp.mid_db = -4.5
    pm.save_preset(amp, "Mon Crunch")
    amp2 = NovaGuitarAmpPlugin()
    assert pm.load_preset(amp2, "Mon Crunch")
    assert abs(amp2.distortion - 0.77) < 1e-4
    assert abs(amp2.mid_db - (-4.5)) < 1e-4

    # Sérialisation complète dans un fichier projet .ndaw
    proj = Project.create_default()
    trk = Track(name="Guitare Lead", track_type="midi", plugin_path="novadaw.guitar", plugin_name="Nova Guitar")
    trk.add_plugin(guitar2)
    trk.add_plugin(amp2)
    proj.tracks.append(trk)

    ndaw_path = tmp_path / "test_guitar.ndaw"
    save_project(proj, str(ndaw_path))
    restored = load_project(str(ndaw_path))
    r_trk = restored.tracks[-1]
    assert r_trk.plugin_path == "novadaw.guitar"
    assert any(isinstance(p, NovaGuitarPlugin) and abs(p.string_tension - 0.41) < 1e-4 for p in r_trk.plugins)
    assert any(isinstance(p, NovaGuitarAmpPlugin) and abs(p.distortion - 0.77) < 1e-4 for p in r_trk.plugins)


def test_audio_engine_offline_chain_guitar_plus_amp():
    proj = Project.create_default()
    proj.bpm = 120.0
    trk = Track(name="Guitare Élec", track_type="midi", plugin_path="novadaw.guitar", plugin_name="Nova Guitar")
    guitar = NovaGuitarPlugin()
    guitar.apply_preset("Électrique Tele Twang (Chevalet)")
    amp = NovaGuitarAmpPlugin()
    amp.apply_preset("Blues Crunch")
    trk.add_plugin(guitar)
    trk.add_plugin(amp)

    clip = MidiClip(start_beat=0.0, length_beats=4.0)
    clip.notes.append(MidiNote(pitch=40, start_beat=0.0, duration=1.5, velocity=105))
    clip.notes.append(MidiNote(pitch=47, start_beat=0.5, duration=1.5, velocity=100))
    trk.clips.append(clip)
    proj.tracks.append(trk)

    engine = AudioEngine(proj)
    sig1 = engine._fast_inst_signature(trk)
    assert sig1 is not None
    guitar.string_tension = 0.25
    sig2 = engine._fast_inst_signature(trk)
    assert sig2 != sig1

    rendered = engine._render_track_offline(trk, bpm=120.0, sample_rate=44100, end_beat=4.0)
    assert rendered is not None
    assert rendered.shape[1] == 2
    assert np.all(np.isfinite(rendered))
    assert float(np.max(np.abs(rendered))) > 0.02


def test_gui_editors_instantiate_and_sync():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])

    guitar = NovaGuitarPlugin()
    g_ed = guitar.create_editor()
    assert g_ed is not None
    guitar.string_tension = 0.88
    guitar.enabled = False
    g_ed._sync_all()
    assert g_ed.btn_bypass.isChecked() is True
    assert g_ed._sliders["string_tension"][0].value() == 88
    g_ed.close()

    amp = NovaGuitarAmpPlugin()
    a_ed = amp.create_editor()
    assert a_ed is not None
    amp.distortion = 0.64
    amp.enabled = False
    a_ed._sync_all()
    assert a_ed.btn_bypass.isChecked() is True
    assert a_ed._sliders["distortion"][0].value() == 64
    a_ed.close()

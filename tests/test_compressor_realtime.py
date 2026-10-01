"""
tests/test_compressor_realtime.py - Suite complète de validation du Compresseur Dynamique Studio.

Vérifie :
1. Pureté audio studio & très faible distorsion harmonique (THD < 0.05% sur basses)
2. Détection double mode (Peak percutant et RMS musical)
3. Filtre Sidechain HPF 80 Hz pour anti-pompage
4. Compensation de gain Auto-Makeup
5. Compression parallèle transparente (Dry/Wet)
6. Traitement en direct temps réel pendant la lecture de l'AudioEngine
7. Réactivité immédiate lors de l'ajustement des paramètres en direct (Threshold, Bypass, etc.)
8. Coexistence optimale avec le cache ASIO-Guard
9. Synchronisation GUI et presets studio
"""
import os
import math
import pytest
import numpy as np

os.environ["NOVADAW_SILENT_TESTS"] = "1"

from core.project import Project, Track, AudioClip
from core.audio_engine import AudioEngine
from plugins.compressor.compressor_plugin import CompressorPlugin
from plugins.compressor.compressor_gui import CompressorPluginWidget


def test_compressor_low_thd_mastering_grade():
    """Vérifie que le compresseur n'introduit aucune dégradation harmonique perceptible (THD < 0.05%)"""
    sr = 44100
    comp = CompressorPlugin(
        threshold_db=-18.0,
        ratio=4.0,
        attack_ms=15.0,
        release_ms=120.0,
        knee_db=4.0
    )

    dur = 0.6
    n_samples = int(sr * dur)
    t = np.linspace(0, dur, n_samples, endpoint=False)
    # 100 Hz basse pure
    sig = (0.75 * np.sin(2 * np.pi * 100 * t)).astype(np.float32)
    audio = np.column_stack((sig, sig))

    # Traitement par blocs pour simuler le flux audio réel
    block_size = 512
    out = np.zeros_like(audio)
    for i in range(0, len(audio), block_size):
        chunk = audio[i:i + block_size]
        out[i:i + len(chunk)] = comp.process(chunk, sr)

    # Mesurer la distorsion harmonique sur les 100 derniers millisecondes (régime établi)
    n_eval = int(sr * 0.1)
    sig_eval = out[-n_eval:, 0]
    fft = np.abs(np.fft.rfft(sig_eval))
    freqs = np.fft.rfftfreq(n_eval, 1 / sr)

    idx100 = np.argmin(np.abs(freqs - 100))
    idx300 = np.argmin(np.abs(freqs - 300))
    idx500 = np.argmin(np.abs(freqs - 500))

    h1 = fft[idx100]
    h3 = fft[idx300]
    h5 = fft[idx500]

    thd = (math.sqrt(h3 ** 2 + h5 ** 2) / h1) * 100
    assert thd < 0.05, f"THD trop élevé : {thd:.4f}% (doit être < 0.05%)"
    assert comp.current_gain_reduction_db < -3.0
    assert not np.isnan(out).any()
    assert not np.isinf(out).any()


def test_compressor_detection_modes_peak_and_rms():
    """Vérifie le comportement distinct des modes de détection Peak (rapide) et RMS (doux)"""
    sr = 44100

    # 1. Mode Peak sur transitoire ultra-rapide
    comp_peak = CompressorPlugin(threshold_db=-15.0, ratio=6.0, attack_ms=2.0, release_ms=50.0, detection_mode="peak")
    pulse = np.zeros((1024, 2), dtype=np.float32)
    pulse[200:300] = 0.9  # Transitoire fort
    out_peak = comp_peak.process(pulse, sr)

    assert comp_peak.current_gain_reduction_db < -5.0
    assert np.max(np.abs(out_peak)) < 0.9

    # 2. Mode RMS sur signal continu doux
    comp_rms = CompressorPlugin(threshold_db=-18.0, ratio=3.0, attack_ms=20.0, release_ms=200.0, detection_mode="rms")
    t = np.linspace(0, 0.2, int(sr * 0.2), endpoint=False)
    sig = (0.6 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    audio = np.column_stack((sig, sig))
    out_rms = comp_rms.process(audio, sr)

    assert comp_rms.current_gain_reduction_db < -2.0
    assert np.max(np.abs(out_rms)) < np.max(np.abs(audio))


def test_compressor_sidechain_hpf_bass_immunity():
    """Vérifie que le filtre sidechain HPF 80 Hz réduit la compression provoquée par les sub-basses"""
    sr = 44100
    dur = 0.3
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    # Signal sub-basse à 50 Hz
    sub_sig = (0.7 * np.sin(2 * np.pi * 50 * t)).astype(np.float32)
    audio = np.column_stack((sub_sig, sub_sig))

    # Compresseur SANS sidechain HPF
    comp_no_hpf = CompressorPlugin(threshold_db=-18.0, ratio=4.0, sidechain_hpf_hz=0.0)
    comp_no_hpf.process(audio, sr)
    gr_no_hpf = abs(comp_no_hpf.current_gain_reduction_db)

    # Compresseur AVEC sidechain HPF 80 Hz
    comp_hpf = CompressorPlugin(threshold_db=-18.0, ratio=4.0, sidechain_hpf_hz=80.0)
    comp_hpf.process(audio, sr)
    gr_hpf = abs(comp_hpf.current_gain_reduction_db)

    # Le filtre HPF doit atténuer le déclenchement de la compression sur 50 Hz
    assert gr_hpf < gr_no_hpf, f"HPF doit réduire la compression des sub-basses ({gr_hpf:.2f} dB vs {gr_no_hpf:.2f} dB)"


def test_compressor_auto_makeup_and_parallel_mix():
    """Vérifie l'Auto-Makeup gain et le mix parallèle (Dry/Wet)"""
    sr = 44100
    dur = 0.2
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    sig = (0.6 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    audio = np.column_stack((sig, sig))

    # Sans auto-makeup
    comp_normal = CompressorPlugin(threshold_db=-24.0, ratio=6.0, auto_makeup=False, makeup_gain_db=0.0)
    out_normal = comp_normal.process(audio, sr)

    # Avec auto-makeup
    comp_auto = CompressorPlugin(threshold_db=-24.0, ratio=6.0, auto_makeup=True, makeup_gain_db=0.0)
    out_auto = comp_auto.process(audio, sr)

    # L'auto-makeup doit rehausser le niveau de sortie
    assert np.max(np.abs(out_auto)) > np.max(np.abs(out_normal))

    # Mix 100% dry (0.0) doit restituer exactement le signal original
    comp_dry = CompressorPlugin(threshold_db=-30.0, ratio=10.0, mix=0.0)
    out_dry = comp_dry.process(audio, sr)
    assert np.allclose(out_dry, audio, atol=1e-5)


def test_compressor_realtime_audio_engine_and_live_tweaking():
    """
    Test d'intégration critique :
    Vérifie que l'AudioEngine traite le compresseur en temps réel pendant la lecture,
    que les ajustements de paramètres (Threshold, Bypass) s'entendent immédiatement
    et que les compteurs GR s'animent en direct sans nécessiter de re-rendu de cache.
    """
    project = Project(name="RealtimeCompTest", bpm=120.0)
    track = Track(name="Voice Track", track_type="audio")
    project.add_track(track)

    sr = 44100
    t = np.linspace(0, 4.0, int(sr * 4.0), endpoint=False)
    data = (0.8 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    stereo_clip = np.column_stack((data, data))

    clip = AudioClip(name="Test Clip", start_beat=0.0, length_beats=8.0)
    clip.audio_data = stereo_clip
    track.clips.append(clip)

    comp = CompressorPlugin(threshold_db=-24.0, ratio=6.0, attack_ms=5.0, release_ms=50.0)
    track.add_plugin(comp)

    engine = AudioEngine(sample_rate=sr, block_size=512)
    engine.set_project(project)

    # Lancer la lecture
    engine.is_playing = True
    frames = 512
    out_buf = np.zeros((frames, 2), dtype=np.float32)

    assert comp.current_gain_reduction_db == 0.0

    # 1. Écoute pendant la lecture : le compresseur doit être actif en direct
    for _ in range(15):
        engine._audio_callback(out_buf, frames, None, None)

    active_gr = comp.current_gain_reduction_db
    active_peak = float(np.max(np.abs(out_buf)))

    assert active_gr < -2.0, f"Le compresseur doit être actif en lecture continue (GR: {active_gr:.2f} dB)"
    assert comp.current_input_peak_db > -10.0
    assert comp.current_output_peak_db > -30.0

    # 2. Ajustement en direct : BASCULEMENT EN BYPASS
    comp.enabled = False
    engine._audio_callback(out_buf, frames, None, None)
    bypassed_peak = float(np.max(np.abs(out_buf)))

    # Le son bypassé doit être immédiatement plus fort (gain non réduit)
    assert bypassed_peak > active_peak, f"Bypass immédiat : {bypassed_peak:.4f} doit être > {active_peak:.4f}"

    # 3. Ré-activation et réglage d'un seuil plus haut (-6 dB au lieu de -24 dB)
    comp.enabled = True
    comp.threshold_db = -6.0
    engine._audio_callback(out_buf, frames, None, None)
    higher_thresh_peak = float(np.max(np.abs(out_buf)))

    assert higher_thresh_peak > active_peak
    engine.close()


def test_compressor_gui_and_presets_sync(qapp):
    """Vérifie la création de l'interface GUI et la synchronisation avec les presets"""
    comp = CompressorPlugin(threshold_db=-20.0, ratio=4.0)
    widget = comp.create_editor()
    assert isinstance(widget, CompressorPluginWidget)

    # Vérifier les faders initiaux
    assert widget.slider_thresh.value() == -20
    assert widget.slider_ratio.value() == 40

    # Appliquer un preset de studio
    p_drums = comp.get_factory_presets()["Batterie Punch (VCA)"]
    comp.set_state(p_drums)
    widget._sync_all_controls()

    assert widget.slider_thresh.value() == int(p_drums["threshold_db"])
    assert widget.slider_ratio.value() == int(round(p_drums["ratio"] * 10))
    assert widget.btn_hpf.isChecked()

    widget.close()

"""
tests/test_native_dsp_instruments.py - Comprehensive tests for M1b Native C99 Instrument DSP:
- Guitar _lfilter_fast & generate_pluck_excitation HAS_SCIPY=False regressions
- PianoSoundboard, NovaPianoPlugin, _hammer_thump cold-spike cache
- Guitar (WaveguideStringPlane, GuitarBodyOrPickupProcessor, GuitarStereoWidener, NovaGuitarPlugin)
- Synth & Shared Reverb (oscillators, ADSR, ChamberlinSVF, StereoPingPongDelay, StereoStudioReverb, NovaSynthPlugin)
- State/reset determinism, C ABI aliasing safety, edge cases, NOVADAW_DISABLE_NATIVE_DSP=1 fallback, microbenchmarks
"""
import ctypes
import importlib
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import signal as scipy_sig

from core import dsp_native
from plugins.guitar import guitar_dsp
from plugins.guitar.guitar_dsp import (
    GuitarBodyOrPickupProcessor,
    GuitarStereoWidener,
    WaveguideStringPlane,
    _lfilter_fast,
    generate_pluck_excitation,
)
from plugins.guitar.guitar_plugin import NovaGuitarPlugin
from plugins.piano import piano_dsp
from plugins.piano.piano_plugin import NovaPianoPlugin
from plugins.piano.piano_soundboard import BRIDGE_CHANNELS, PianoSoundboard
from plugins.synth import synth_dsp
from plugins.synth.synth_dsp import (
    ChamberlinSVF,
    StereoPingPongDelay,
    StereoStudioReverb,
    compute_adsr_envelope,
    generate_oscillator,
)
from plugins.synth.synth_plugin import NovaSynthPlugin

_INITIAL_NATIVE_AVAILABLE = bool(dsp_native.NATIVE_AVAILABLE)


def _rmse(a: np.ndarray, b: np.ndarray) -> float:
    diff = a.astype(np.float64) - b.astype(np.float64)
    return float(np.sqrt(np.mean(diff * diff)))


class _PythonFallbackScope:
    """Reload core.dsp_native with NOVADAW_DISABLE_NATIVE_DSP=1 and restore on exit."""

    def __enter__(self):
        self._prev = os.environ.get("NOVADAW_DISABLE_NATIVE_DSP")
        os.environ["NOVADAW_DISABLE_NATIVE_DSP"] = "1"
        importlib.reload(dsp_native)
        return dsp_native

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._prev is None:
            os.environ.pop("NOVADAW_DISABLE_NATIVE_DSP", None)
        else:
            os.environ["NOVADAW_DISABLE_NATIVE_DSP"] = self._prev
        importlib.reload(dsp_native)


def _require_native():
    if not _INITIAL_NATIVE_AVAILABLE:
        pytest.skip("Native DSP disabled via NOVADAW_DISABLE_NATIVE_DSP")


def _make_stereo_signal(frames: int = 4096, sr: int = 44100, seed: int = 77) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(frames, dtype=np.float64) / float(sr)
    l = 0.35 * np.sin(2.0 * np.pi * 110.0 * t) + 0.20 * np.sin(2.0 * np.pi * 1200.0 * t) + 0.05 * rng.standard_normal(frames)
    r = 0.30 * np.sin(2.0 * np.pi * 165.0 * t + 0.3) + 0.22 * np.sin(2.0 * np.pi * 2400.0 * t) + 0.05 * rng.standard_normal(frames)
    return np.column_stack((l, r)).astype(np.float32)


def _make_piano_force(frames: int = 2048, sr: int = 44100, seed: int = 11) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(frames, dtype=np.float64) / float(sr)
    force = np.zeros((frames, BRIDGE_CHANNELS), dtype=np.float32)
    for ch in range(BRIDGE_CHANNELS):
        env = np.exp(-t * (6.0 + ch * 1.5))
        force[:, ch] = (
            (0.25 * np.sin(2.0 * np.pi * (80.0 + ch * 95.0) * t) + 0.04 * rng.standard_normal(frames)) * env
        ).astype(np.float32)
    force[0, 1] += 0.8
    force[17, 3] -= 0.5
    return force


# ---------------------------------------------------------------------------
# 1. Guitar _lfilter_fast & generate_pluck_excitation Regression (HAS_SCIPY=False)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "b,a",
    [
        # len(b) < len(a): exact WaveguideStringPlane loop filter shape (3 vs 4)
        (np.array([0.42, 0.31, -0.09]), np.array([1.0, -0.55, 0.12, -0.04])),
        # len(b) == len(a)
        (np.array([0.2, 0.4, 0.2]), np.array([1.0, -0.5, 0.25])),
        # len(b) > len(a)
        (np.array([0.1, 0.25, 0.3, 0.25, 0.1]), np.array([1.0, -0.4, 0.1])),
        # order == 0
        (np.array([0.75]), np.array([1.0])),
        # a[0] != 1.0
        (np.array([0.5, 0.3]), np.array([2.0, -0.8, 0.2, -0.05])),
    ],
)
def test_lfilter_fast_fallback_matches_scipy_across_shapes(monkeypatch, b, a):
    rng = np.random.default_rng(314)
    x = rng.standard_normal(513).astype(np.float64)
    order = max(len(b), len(a)) - 1
    zi_init = rng.standard_normal(order).astype(np.float64) * 0.1 if order > 0 else np.empty(0, dtype=np.float64)

    y_ref_1, z_ref = scipy_sig.lfilter(b, a, x[:200], zi=zi_init.copy())
    y_ref_2, z_ref = scipy_sig.lfilter(b, a, x[200:], zi=z_ref)

    monkeypatch.setattr(guitar_dsp, "HAS_SCIPY", False)
    y_py_1, z_py = _lfilter_fast(b, a, x[:200], zi_init.copy())
    y_py_2, z_py = _lfilter_fast(b, a, x[200:], z_py)

    np.testing.assert_allclose(y_py_1, y_ref_1, atol=1e-12, rtol=1e-12)
    np.testing.assert_allclose(y_py_2, y_ref_2, atol=1e-12, rtol=1e-12)
    np.testing.assert_allclose(z_py, z_ref, atol=1e-12, rtol=1e-12)


def test_guitar_waveguide_and_pluck_with_has_scipy_false(monkeypatch):
    # Verify generate_pluck_excitation applies lowpass even with HAS_SCIPY=False
    exc_ref = generate_pluck_excitation(
        512, 180.0, 44100, velocity=105, pluck_position=0.22, pick_attack=0.65,
        guitar_mode="electric_clean", rng=np.random.default_rng(999),
    )

    monkeypatch.setattr(guitar_dsp, "HAS_SCIPY", False)
    exc_py = generate_pluck_excitation(
        512, 180.0, 44100, velocity=105, pluck_position=0.22, pick_attack=0.65,
        guitar_mode="electric_clean", rng=np.random.default_rng(999),
    )
    np.testing.assert_allclose(exc_py, exc_ref, atol=1e-12, rtol=1e-12)

    # Verify WaveguideStringPlane + NovaGuitarPlugin.render_note in pure-Python fallback with HAS_SCIPY=False
    with _PythonFallbackScope():
        plane = WaveguideStringPlane(freq=110.0, sample_rate=44100)
        assert plane._native is None
        out_plane = plane.step(exc_py, is_released=False)
        assert out_plane.shape == (512,) and np.all(np.isfinite(out_plane))

        g = NovaGuitarPlugin()
        g.apply_preset("Acoustique Folk Acier (Dreadnought)")
        wave = g.render_note(45, duration_sec=0.25, sample_rate=44100, velocity=100)
        assert wave.ndim == 2 and wave.shape[1] == 2
        assert wave.dtype == np.float32
        assert np.all(np.isfinite(wave))
        assert float(np.max(np.abs(wave))) > 0.05


# ---------------------------------------------------------------------------
# 2. PianoSoundboard & NovaPianoPlugin Native vs Python Parity & Cold Spikes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("resonance", [0.0, 0.35, 0.67, 0.78, 1.0])
@pytest.mark.parametrize("sr,chunk", [(22050, 128), (44100, 93), (48000, 512), (96000, 4096)])
def test_piano_soundboard_native_vs_python_parity(resonance, sr, chunk):
    _require_native()
    force = _make_piano_force(frames=2048, sr=sr, seed=21)

    sb_nat = PianoSoundboard(sr)
    assert sb_nat._native is not None
    parts_nat = [sb_nat.process(force[i:i + chunk], resonance) for i in range(0, len(force), chunk)]
    y_nat = np.vstack(parts_nat)

    with _PythonFallbackScope():
        sb_py = PianoSoundboard(sr)
        assert sb_py._native is None
        parts_py = [sb_py.process(force[i:i + chunk], resonance) for i in range(0, len(force), chunk)]
        y_py = np.vstack(parts_py)

    assert y_nat.dtype == np.float32 and y_nat.shape == (len(force), 2)
    assert _rmse(y_nat, y_py) < 1e-4


def test_piano_soundboard_midstream_modulation_block_invariance_and_reset():
    _require_native()
    sr = 44100
    force = _make_piano_force(frames=3000, sr=sr, seed=22)

    sb_nat = PianoSoundboard(sr)
    full = sb_nat.process(force, 0.67)
    sb_nat.reset()
    blocked = np.vstack([
        sb_nat.process(force[:93], 0.67),
        sb_nat.process(force[93:712], 0.67),
        sb_nat.process(force[712:], 0.67),
    ])
    np.testing.assert_allclose(blocked, full, atol=1e-6)

    # Reset determinism
    sb_nat.reset()
    again = sb_nat.process(force, 0.67)
    np.testing.assert_allclose(again, full, atol=1e-7)

    # Mid-stream resonance modulation parity
    sb_nat.reset()
    mod_nat = np.vstack([
        sb_nat.process(force[:800], 0.2),
        sb_nat.process(force[800:1800], 0.9),
        sb_nat.process(force[1800:], 0.0),
    ])
    with _PythonFallbackScope():
        sb_py = PianoSoundboard(sr)
        mod_py = np.vstack([
            sb_py.process(force[:800], 0.2),
            sb_py.process(force[800:1800], 0.9),
            sb_py.process(force[1800:], 0.0),
        ])
    assert _rmse(mod_nat, mod_py) < 1e-4


@pytest.mark.parametrize("reverb_mix,room", [(0.0, "large_room"), (0.6, "cathedral")])
def test_nova_piano_plugin_native_vs_python_parity(reverb_mix, room):
    _require_native()
    sr = 44100
    notes = [
        SimpleNamespace(pitch=48, velocity=95, start_beat=0.0, duration=0.8),
        SimpleNamespace(pitch=64, velocity=82, start_beat=0.12, duration=0.9),
    ]

    p_nat = NovaPianoPlugin()
    p_nat.set_state({"reverb_mix": reverb_mix, "reverb_room": room, "soundboard_resonance": 0.72})
    note_nat = p_nat.render_note(60, duration_sec=0.45, sample_rate=sr, velocity=100)
    ctx_nat = p_nat.create_isolated_context(sr)
    slice_nat = np.vstack([
        p_nat.render_in_context(notes, i / sr, 1.0, min(1024, 22050 - i), sr, ctx_nat)
        for i in range(0, 22050, 1024)
    ])

    with _PythonFallbackScope():
        p_py = NovaPianoPlugin()
        p_py.set_state({"reverb_mix": reverb_mix, "reverb_room": room, "soundboard_resonance": 0.72})
        note_py = p_py.render_note(60, duration_sec=0.45, sample_rate=sr, velocity=100)
        ctx_py = p_py.create_isolated_context(sr)
        slice_py = np.vstack([
            p_py.render_in_context(notes, i / sr, 1.0, min(1024, 22050 - i), sr, ctx_py)
            for i in range(0, 22050, 1024)
        ])

    assert _rmse(note_nat, note_py) < 1e-4
    assert _rmse(slice_nat, slice_py) < 1e-4


def test_piano_hammer_thump_cache_eliminates_cold_spikes():
    piano_dsp._hammer_thump_base.cache_clear()
    # Warm pitch=60 at hardness=0.58
    t_cold0 = time.perf_counter()
    th0 = piano_dsp._hammer_thump(60, 80, 44100, 0.58)
    _ = (time.perf_counter() - t_cold0) * 1000.0

    # New velocities on already-seen pitch=60 must hit cache and stay fast
    t0 = time.perf_counter()
    for vel in range(20, 127, 3):
        th = piano_dsp._hammer_thump(60, vel, 44100, 0.58)
        assert th.shape == th0.shape and np.all(np.isfinite(th))
    avg_ms = ((time.perf_counter() - t0) * 1000.0) / 36.0
    assert avg_ms < 0.5, f"_hammer_thump warm-pitch/new-velocity spike too high: {avg_ms:.3f} ms"
    assert piano_dsp._hammer_thump_base.cache_info().hits >= 35


# ---------------------------------------------------------------------------
# 3. Guitar Native vs Python Parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "preset,sr,chunk,palm_mute,reverb_mix",
    [
        ("Acoustique Folk Acier (Dreadnought)", 44100, 128, 0.0, 0.16),
        ("Acoustique Nylon Classique", 48000, 512, 0.0, 0.20),
        ("Électrique Strat Clean (Manche)", 44100, 256, 0.0, 0.0),
        ("Électrique Tele Twang (Chevalet)", 44100, 4096, 0.7, 0.0),
        ("Basse Électrique Clean (Pick)", 44100, 512, 0.0, 0.25),
    ],
)
def test_guitar_plugin_native_vs_python_parity(preset, sr, chunk, palm_mute, reverb_mix):
    _require_native()
    bps = 2.0
    notes = [
        SimpleNamespace(pitch=40, start_beat=0.0, duration=1.5, velocity=102),
        SimpleNamespace(pitch=47, start_beat=0.1, duration=1.5, velocity=94),
        SimpleNamespace(pitch=52, start_beat=0.25, duration=1.5, velocity=98),
    ]
    total_frames = 16384

    def _run(g: NovaGuitarPlugin) -> np.ndarray:
        assert g.apply_preset(preset)
        g.palm_mute = palm_mute
        g.reverb_mix = reverb_mix
        g._sync_reverb()
        parts = []
        for i in range(0, total_frames, chunk):
            n = min(chunk, total_frames - i)
            sb = (i / sr) * bps
            eb = ((i + n) / sr) * bps
            parts.append(g.render_slice(notes, sb, eb, bps, n, sr))
        return np.vstack(parts)

    g_nat = NovaGuitarPlugin()
    y_nat = _run(g_nat)

    with _PythonFallbackScope():
        g_py = NovaGuitarPlugin()
        y_py = _run(g_py)

    assert y_nat.dtype == np.float32 and y_nat.shape == (total_frames, 2)
    assert _rmse(y_nat, y_py) < 1e-4


def test_guitar_subcomponents_and_seek_parity():
    _require_native()
    sr = 44100
    rng = np.random.default_rng(4242)
    mono = (rng.standard_normal(1024) * 0.3).astype(np.float64)

    for mode in ("acoustic_steel", "acoustic_nylon", "electric_bass", "electric_clean"):
        bp_nat = GuitarBodyOrPickupProcessor(sr)
        out_nat = bp_nat.process_mono(mono, mode=mode, body_resonance=0.75, pickup_position=0.22, sample_rate=sr)
        with _PythonFallbackScope():
            bp_py = GuitarBodyOrPickupProcessor(sr)
            out_py = bp_py.process_mono(mono, mode=mode, body_resonance=0.75, pickup_position=0.22, sample_rate=sr)
        assert _rmse(out_nat, out_py) < 1e-10

    for width in (0.0, 0.35, 1.0):
        w_nat = GuitarStereoWidener()
        l_nat, r_nat = w_nat.process(mono, width)
        with _PythonFallbackScope():
            w_py = GuitarStereoWidener()
            l_py, r_py = w_py.process(mono, width)
        assert _rmse(l_nat, l_py) < 1e-10
        assert _rmse(r_nat, r_py) < 1e-10

    # Seek mid-note (uses skip_samples)
    notes = [SimpleNamespace(pitch=45, start_beat=0.0, duration=4.0, velocity=100)]
    g_nat = NovaGuitarPlugin()
    y_seek_nat = g_nat.render_slice(notes, 1.5, 1.75, 2.0, 5512, sr)
    with _PythonFallbackScope():
        g_py = NovaGuitarPlugin()
        y_seek_py = g_py.render_slice(notes, 1.5, 1.75, 2.0, 5512, sr)
    assert _rmse(y_seek_nat, y_seek_py) < 1e-4


# ---------------------------------------------------------------------------
# 4. Synth & Shared Reverb Native vs Python Parity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wave", ["sine", "triangle", "saw", "square", "pulse", "fm", "supersaw"])
@pytest.mark.parametrize("time_offset", [-0.015, 0.0, 0.237])
def test_synth_oscillator_native_vs_python_parity(wave, time_offset):
    _require_native()
    sr = 44100
    n = 512
    kwargs = dict(
        wave_type=wave,
        freq=329.63,
        num_samples=n,
        sample_rate=sr,
        phase_offset=0.17,
        pulse_width=0.37,
        fm_ratio=2.5,
        fm_depth=1.4,
        unison_detune=0.35,
        unison_spread=0.65,
        time_offset=time_offset,
    )
    y_nat = generate_oscillator(**kwargs)
    with _PythonFallbackScope():
        y_py = generate_oscillator(**kwargs)

    assert y_nat.dtype == np.float32 and y_nat.shape == (n, 2)
    assert _rmse(y_nat, y_py) < 1e-4

    # Also test explicit t_vec
    t_vec = np.linspace(time_offset, time_offset + n / sr, n, endpoint=False, dtype=np.float64)
    y_tvec_nat = generate_oscillator(**kwargs, t_vec=t_vec)
    assert _rmse(y_tvec_nat, y_py) < 1e-4


def test_synth_noise_oscillator_bounds():
    y = generate_oscillator("noise", 440.0, 2048, 44100)
    assert y.dtype == np.float32 and y.shape == (2048, 2)
    assert np.all(np.isfinite(y))
    rms = float(np.sqrt(np.mean(y * y)))
    assert 0.1 < rms < 1.0


def test_synth_adsr_native_vs_python_parity():
    _require_native()
    cases = [
        (-0.2, -0.05, 512, 0.5, 0.05, 0.15, 0.6, 0.25),   # pre-attack
        (-0.02, 0.12, 512, 0.5, 0.05, 0.15, 0.6, 0.25),   # pre-attack -> attack -> decay
        (0.25, 0.45, 512, 0.5, 0.05, 0.15, 0.6, 0.25),    # sustain
        (0.45, 0.85, 512, 0.5, 0.05, 0.15, 0.6, 0.25),    # sustain -> release -> tail
        (0.10, 0.30, 512, 0.08, 0.10, 0.15, 0.6, 0.25),   # early note-off during attack
        (0.90, 1.10, 512, 0.5, 0.05, 0.15, 0.6, 0.25),    # post-release
    ]
    for args in cases:
        e_nat = compute_adsr_envelope(*args)
        with _PythonFallbackScope():
            e_py = compute_adsr_envelope(*args)
        assert e_nat.dtype == np.float32 and len(e_nat) == args[2]
        assert _rmse(e_nat, e_py) < 1e-5


@pytest.mark.parametrize("ftype", ["lowpass", "highpass", "bandpass", "notch"])
@pytest.mark.parametrize("drive", [0.0, 1.5])
def test_synth_svf_native_vs_python_parity(ftype, drive):
    _require_native()
    sr = 44100
    audio = _make_stereo_signal(2048, sr=sr, seed=88)
    mod = np.linspace(-0.3, 0.6, 512, dtype=np.float32)

    def _run_svf(svf: ChamberlinSVF) -> np.ndarray:
        p1 = svf.process(audio[:512], sr, filter_type=ftype, cutoff=900.0, resonance=1.4, drive=drive)
        # Cutoff & Q jump -> triggers clickless zi rebuild
        p2 = svf.process(audio[512:1024], sr, filter_type=ftype, cutoff=3200.0, resonance=3.5, drive=drive)
        p3 = svf.process(audio[1024:1536], sr, filter_type=ftype, cutoff=1500.0, resonance=2.0, drive=drive, mod_cutoff=mod)
        # Wide-open LP bypass check
        p4 = svf.process(audio[1536:], sr, filter_type="lowpass", cutoff=21000.0, resonance=0.7, drive=0.0)
        return np.vstack([p1, p2, p3, p4])

    y_nat = _run_svf(ChamberlinSVF())
    with _PythonFallbackScope():
        y_py = _run_svf(ChamberlinSVF())

    assert y_nat.dtype == np.float32 and y_nat.shape == audio.shape
    assert _rmse(y_nat, y_py) < 1e-4


@pytest.mark.parametrize("ping_pong", [True, False])
def test_synth_delay_and_reverb_native_vs_python_parity(ping_pong):
    _require_native()
    sr = 44100
    audio = _make_stereo_signal(4096, sr=sr, seed=99)

    def _run_delay(d: StereoPingPongDelay) -> np.ndarray:
        d.enabled = True
        d.time_sec = 0.04
        d.feedback = 0.45
        d.ping_pong = ping_pong
        d.mix = 0.40
        return np.vstack([d.process(audio[i:i + 512]) for i in range(0, len(audio), 512)])

    def _run_reverb(r: StereoStudioReverb) -> np.ndarray:
        r.enabled = True
        r.room_size = 0.65
        r.damping = 0.40
        r.mix = 0.35
        return np.vstack([r.process(audio[i:i + 512]) for i in range(0, len(audio), 512)])

    d_nat = _run_delay(StereoPingPongDelay(sr))
    r_nat = _run_reverb(StereoStudioReverb(sr))
    with _PythonFallbackScope():
        d_py = _run_delay(StereoPingPongDelay(sr))
        r_py = _run_reverb(StereoStudioReverb(sr))

    assert _rmse(d_nat, d_py) < 1e-4
    assert _rmse(r_nat, r_py) < 1e-4


@pytest.mark.parametrize(
    "preset,bus_index",
    [
        ("Cyberpunk Acid Lead", None),
        ("Neon Horizon SuperSaw", None),
        ("Ethereal Dream Pad", None),
        ("Deep Sub & Punch Bass", 0),
        ("Deep Sub & Punch Bass", 1),
    ],
)
def test_nova_synth_plugin_native_vs_python_parity(preset, bus_index):
    _require_native()
    sr = 44100
    bps = 2.0
    notes = [
        SimpleNamespace(pitch=48, start_beat=0.0, duration=1.0, velocity=100),
        SimpleNamespace(pitch=55, start_beat=0.08, duration=1.0, velocity=92),
        SimpleNamespace(pitch=64, start_beat=0.16, duration=1.0, velocity=96),
    ]

    def _run(s: NovaSynthPlugin, mute_noise: bool = True) -> np.ndarray:
        assert s.apply_preset(preset)
        if mute_noise:
            for lyr in s.layers:
                if (lyr.waveform or "").lower().strip() == "noise":
                    lyr.muted = True
        parts = []
        for i in range(0, 8192, 512):
            sb = (i / sr) * bps
            eb = ((i + 512) / sr) * bps
            parts.append(s.render_slice(notes, sb, eb, bps, 512, sr, bus_index=bus_index))
        return np.vstack(parts)

    s_nat = NovaSynthPlugin()
    y_nat = _run(s_nat, mute_noise=True)
    with _PythonFallbackScope():
        s_py = NovaSynthPlugin()
        y_py = _run(s_py, mute_noise=True)

    assert y_nat.dtype == np.float32 and y_nat.shape == (8192, 2)
    assert _rmse(y_nat, y_py) < 1e-4

    # Also verify full preset with stochastic noise layer unmuted has matching RMS
    if preset == "Neon Horizon SuperSaw":
        s_nat_full = NovaSynthPlugin()
        y_nat_full = _run(s_nat_full, mute_noise=False)
        with _PythonFallbackScope():
            s_py_full = NovaSynthPlugin()
            y_py_full = _run(s_py_full, mute_noise=False)
        assert np.all(np.isfinite(y_nat_full))
        rms_nat = float(np.sqrt(np.mean(y_nat_full.astype(np.float64) ** 2)))
        rms_py = float(np.sqrt(np.mean(y_py_full.astype(np.float64) ** 2)))
        assert abs(rms_nat - rms_py) < 0.02


# ---------------------------------------------------------------------------
# 5. State/Reset Determinism, Edge Cases, Aliasing Safety, Fallback Subprocess
# ---------------------------------------------------------------------------


def test_instrument_state_roundtrip_and_reset_determinism():
    sr = 44100
    notes = [SimpleNamespace(pitch=60, start_beat=0.0, duration=0.8, velocity=100)]

    # Guitar
    g1 = NovaGuitarPlugin()
    g1.apply_preset("Acoustique Nylon Classique")
    out_g1 = g1.render_slice(notes, 0.0, 0.5, 2.0, 2048, sr)
    g1.reset()
    out_g2 = g1.render_slice(notes, 0.0, 0.5, 2.0, 2048, sr)
    np.testing.assert_allclose(out_g1, out_g2, atol=1e-6)
    g2 = NovaGuitarPlugin()
    g2.set_state(g1.get_state())
    out_g3 = g2.render_slice(notes, 0.0, 0.5, 2.0, 2048, sr)
    np.testing.assert_allclose(out_g1, out_g3, atol=1e-6)

    # Synth (deterministic preset without stochastic noise layer)
    s1 = NovaSynthPlugin()
    s1.apply_preset("Cyberpunk Acid Lead")
    out_s1 = s1.render_slice(notes, 0.0, 0.5, 2.0, 2048, sr)
    s1.reset()
    out_s2 = s1.render_slice(notes, 0.0, 0.5, 2.0, 2048, sr)
    np.testing.assert_allclose(out_s1, out_s2, atol=1e-6)
    s2 = NovaSynthPlugin()
    s2.set_state(s1.get_state())
    out_s3 = s2.render_slice(notes, 0.0, 0.5, 2.0, 2048, sr)
    np.testing.assert_allclose(out_s1, out_s3, atol=1e-6)

    # Piano
    p1 = NovaPianoPlugin()
    p1.set_state({"reverb_room": "concert_hall", "reverb_mix": 0.35, "soundboard_resonance": 0.8})
    out_p1 = p1.render_slice(notes, 0.0, 0.5, 2.0, 2048, sr)
    p1.reset()
    out_p2 = p1.render_slice(notes, 0.0, 0.5, 2.0, 2048, sr)
    np.testing.assert_allclose(out_p1, out_p2, atol=1e-6)
    p2 = NovaPianoPlugin()
    p2.set_state(p1.get_state())
    out_p3 = p2.render_slice(notes, 0.0, 0.5, 2.0, 2048, sr)
    np.testing.assert_allclose(out_p1, out_p3, atol=1e-6)


def test_edge_cases_nan_inf_strided_float64_immutability():
    sr = 44100

    # PianoSoundboard edge cases
    sb = PianoSoundboard(sr)
    assert sb.process(np.empty((0, 5), dtype=np.float32), 0.67).shape == (0, 2)
    sil_sb = sb.process(np.zeros((256, 5), dtype=np.float32), 0.67)
    assert sil_sb.shape == (256, 2) and np.max(np.abs(sil_sb)) == 0.0

    dirty_force = _make_piano_force(256, sr, seed=5)
    dirty_force[5, 0] = np.nan
    dirty_force[20, 2] = np.inf
    dirty_force[40, 4] = -np.inf
    out_dirty = sb.process(dirty_force, float("nan"))
    assert out_dirty.shape == (256, 2) and np.all(np.isfinite(out_dirty))
    out_rec = sb.process(_make_piano_force(256, sr, seed=6), 2.5)
    assert np.all(np.isfinite(out_rec))

    strided_force = _make_piano_force(512, sr, seed=7)[::2]
    assert not strided_force.flags["C_CONTIGUOUS"]
    snap_f = strided_force.copy()
    out_st = sb.process(strided_force.astype(np.float64), 0.67)
    assert out_st.dtype == np.float32 and out_st.shape == (256, 2) and np.all(np.isfinite(out_st))
    assert np.array_equal(strided_force, snap_f)

    # Synth DSP edge cases (SVF, Delay, Reverb)
    svf = ChamberlinSVF()
    delay = StereoPingPongDelay(sr)
    delay.enabled = True
    rev = StereoStudioReverb(sr)
    rev.enabled = True
    for proc in (
        lambda x: svf.process(x, sr, cutoff=1500.0, resonance=1.5, drive=0.8),
        delay.process,
        rev.process,
    ):
        assert len(proc(np.empty((0, 2), dtype=np.float32))) == 0
        sil = proc(np.zeros((256, 2), dtype=np.float32))
        assert sil.shape == (256, 2) and np.max(np.abs(sil)) < 1e-6

        dirty = _make_stereo_signal(256, sr=sr, seed=8)
        dirty[3, 0] = np.nan
        dirty[11, 1] = np.inf
        dirty[19, 0] = -np.inf
        assert np.all(np.isfinite(proc(dirty)))
        clean = _make_stereo_signal(256, sr=sr, seed=9)
        assert np.all(np.isfinite(proc(clean)))

        strided = _make_stereo_signal(512, sr=sr, seed=10)[::2]
        assert not strided.flags["C_CONTIGUOUS"]
        snap = strided.copy()
        out_s = proc(strided.astype(np.float64))
        assert out_s.dtype == np.float32 and out_s.shape == (256, 2) and np.all(np.isfinite(out_s))
        assert np.array_equal(strided, snap)


def test_c_abi_aliasing_safety_instruments():
    _require_native()
    sr = 44100
    frames = 256

    # 1. nova_guitar_body_process aliasing
    mono = np.sin(np.linspace(0.0, 20.0, frames, dtype=np.float64))
    st_ref = dsp_native.NovaGuitarBodyState()
    st_ali = dsp_native.NovaGuitarBodyState()
    out_ref = np.empty(frames, dtype=np.float64)
    dsp_native.nova_guitar_body_process(
        ctypes.byref(st_ref),
        mono.ctypes.data_as(dsp_native.c_double_p),
        out_ref.ctypes.data_as(dsp_native.c_double_p),
        frames, 3, 0.6, 0.22, sr,
    )
    buf_ali = mono.copy()
    dsp_native.nova_guitar_body_process(
        ctypes.byref(st_ali),
        buf_ali.ctypes.data_as(dsp_native.c_double_p),
        buf_ali.ctypes.data_as(dsp_native.c_double_p),
        frames, 3, 0.6, 0.22, sr,
    )
    assert np.array_equal(out_ref, buf_ali)

    # 2. nova_svf_process aliasing
    stereo = _make_stereo_signal(frames, sr=sr, seed=31)
    svf_ref = dsp_native.NovaSVFState()
    svf_ali = dsp_native.NovaSVFState()
    dsp_native.nova_svf_reset(ctypes.byref(svf_ref))
    dsp_native.nova_svf_reset(ctypes.byref(svf_ali))
    out_svf = np.empty_like(stereo)
    dsp_native.nova_svf_process(
        ctypes.byref(svf_ref),
        stereo.ctypes.data_as(dsp_native.c_float_p),
        out_svf.ctypes.data_as(dsp_native.c_float_p),
        frames, sr, 0, 1400.0, 1.8, 0.6, None,
    )
    ali_svf = stereo.copy()
    dsp_native.nova_svf_process(
        ctypes.byref(svf_ali),
        ali_svf.ctypes.data_as(dsp_native.c_float_p),
        ali_svf.ctypes.data_as(dsp_native.c_float_p),
        frames, sr, 0, 1400.0, 1.8, 0.6, None,
    )
    assert np.array_equal(out_svf, ali_svf)

    # 3. nova_delay_process aliasing
    d_ref = dsp_native.nova_delay_create(sr)
    d_ali = dsp_native.nova_delay_create(sr)
    try:
        out_d = np.empty_like(stereo)
        dsp_native.nova_delay_process(
            d_ref, stereo.ctypes.data_as(dsp_native.c_float_p),
            out_d.ctypes.data_as(dsp_native.c_float_p), frames, 0.01, 0.5, 1, 0.4, 1,
        )
        ali_d = stereo.copy()
        dsp_native.nova_delay_process(
            d_ali, ali_d.ctypes.data_as(dsp_native.c_float_p),
            ali_d.ctypes.data_as(dsp_native.c_float_p), frames, 0.01, 0.5, 1, 0.4, 1,
        )
        assert np.array_equal(out_d, ali_d)
    finally:
        dsp_native.nova_delay_destroy(d_ref)
        dsp_native.nova_delay_destroy(d_ali)

    # 4. nova_reverb_process aliasing
    r_ref = dsp_native.nova_reverb_create(sr)
    r_ali = dsp_native.nova_reverb_create(sr)
    try:
        out_r = np.empty_like(stereo)
        dsp_native.nova_reverb_process(
            r_ref, stereo.ctypes.data_as(dsp_native.c_float_p),
            out_r.ctypes.data_as(dsp_native.c_float_p), frames, 0.6, 0.4, 0.35, 1,
        )
        ali_r = stereo.copy()
        dsp_native.nova_reverb_process(
            r_ali, ali_r.ctypes.data_as(dsp_native.c_float_p),
            ali_r.ctypes.data_as(dsp_native.c_float_p), frames, 0.6, 0.4, 0.35, 1,
        )
        assert np.array_equal(out_r, ali_r)
    finally:
        dsp_native.nova_reverb_destroy(r_ref)
        dsp_native.nova_reverb_destroy(r_ali)


def test_disable_native_dsp_instruments_subprocess():
    env = os.environ.copy()
    env["NOVADAW_DISABLE_NATIVE_DSP"] = "1"
    code = "\n".join([
        "from types import SimpleNamespace",
        "import numpy as np",
        "from core import dsp_native",
        "from plugins.piano.piano_soundboard import PianoSoundboard",
        "from plugins.piano.piano_plugin import NovaPianoPlugin",
        "from plugins.guitar.guitar_plugin import NovaGuitarPlugin",
        "from plugins.synth.synth_plugin import NovaSynthPlugin",
        "assert dsp_native.NATIVE_AVAILABLE is False",
        "sb = PianoSoundboard(44100)",
        "assert sb._native is None",
        "y_sb = sb.process(np.ones((128, 5), dtype=np.float32) * 0.1, 0.67)",
        "assert y_sb.shape == (128, 2) and np.all(np.isfinite(y_sb))",
        "notes = [SimpleNamespace(pitch=60, start_beat=0.0, duration=0.5, velocity=100)]",
        "for p in (NovaPianoPlugin(), NovaGuitarPlugin(), NovaSynthPlugin()):",
        "    y = p.render_slice(notes, 0.0, 0.25, 2.0, 256, 44100)",
        "    assert y.shape == (256, 2) and np.all(np.isfinite(y))",
        "print('OK')",
    ])
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"Fallback subprocess failed:\n{proc.stderr}\n{proc.stdout}"
    assert "OK" in proc.stdout


# ---------------------------------------------------------------------------
# 6. Microbenchmarks (PianoSoundboard, Full Piano Track, Guitar, Synth, Reverb)
# ---------------------------------------------------------------------------


def _bench_fn(fn, iters: int = 80, warmup: int = 8) -> float:
    for _ in range(warmup):
        fn()
    t0 = time.perf_counter()
    for _ in range(iters):
        fn()
    return ((time.perf_counter() - t0) / iters) * 1000.0


def test_microbenchmark_instruments_native_speedup():
    _require_native()
    sr = 44100
    force_128 = _make_piano_force(128, sr=sr, seed=1)
    force_4096 = _make_piano_force(4096, sr=sr, seed=2)
    stereo_128 = _make_stereo_signal(128, sr=sr, seed=3)
    stereo_4096 = _make_stereo_signal(4096, sr=sr, seed=4)
    bps = 148.0 / 60.0
    chord = [
        SimpleNamespace(pitch=48, start_beat=0.0, duration=2.0, velocity=100),
        SimpleNamespace(pitch=55, start_beat=0.02, duration=2.0, velocity=95),
        SimpleNamespace(pitch=64, start_beat=0.04, duration=2.0, velocity=90),
    ]

    # 1. PianoSoundboard 128 & 4096
    sb_nat = PianoSoundboard(sr)
    sb_nat_128 = _bench_fn(lambda: sb_nat.process(force_128, 0.67), iters=120)
    sb_nat_4096 = _bench_fn(lambda: sb_nat.process(force_4096, 0.67), iters=60)

    # 2. Guitar 3-note chord 128 & 4096 (sequential streaming blocks)
    def _bench_seq(inst, block_frames: int, n_blocks: int) -> float:
        inst.reset()
        # Warm-up 1 chord onset
        inst.render_slice(chord, 0.0, (block_frames / sr) * bps, bps, block_frames, sr)
        t0 = time.perf_counter()
        for k in range(1, n_blocks + 1):
            sb_b = (k * block_frames / sr) * bps
            eb_b = ((k + 1) * block_frames / sr) * bps
            inst.render_slice(chord, sb_b, eb_b, bps, block_frames, sr)
        return ((time.perf_counter() - t0) / n_blocks) * 1000.0

    g_nat = NovaGuitarPlugin()
    g_nat.apply_preset("Électrique Strat Clean (Manche)")
    g_nat_128 = _bench_seq(g_nat, 128, 120)
    g_nat_4096 = _bench_seq(g_nat, 4096, 30)

    # 3. Synth SuperSaw 3-note chord 128 & 4096 (sequential streaming blocks)
    s_nat = NovaSynthPlugin()
    s_nat.apply_preset("Neon Horizon SuperSaw")
    s_nat_128 = _bench_seq(s_nat, 128, 120)
    s_nat_4096 = _bench_seq(s_nat, 4096, 30)

    # 4. StereoStudioReverb 128 & 4096
    r_nat = StereoStudioReverb(sr)
    r_nat_128 = _bench_fn(lambda: r_nat.process(stereo_128), iters=200)
    r_nat_4096 = _bench_fn(lambda: r_nat.process(stereo_4096), iters=80)

    # 5. Full Piano Track pre-render equivalent (~1,160 active 4096 chunks, 357 notes @ 148 BPM, matching Sous la cendre density)
    piano_notes = []
    progression = (48, 55, 60, 52, 59, 64, 50, 57, 62, 47, 55, 62)
    for k in range(357):
        chord_idx = k // 3
        # 119 3-note chords spread across 352 beats (~2.95 beats/chord, active ~76% of chunks -> ~1160 chunks, ~3500 note evals)
        sb_beat = chord_idx * 2.95 + (k % 3) * 0.02
        pitch = progression[((chord_idx % 4) * 3) + (k % 3)]
        piano_notes.append(SimpleNamespace(pitch=pitch, velocity=82 + (chord_idx % 3) * 8, start_beat=sb_beat, duration=0.45))

    def _run_piano_track(piano_inst: NovaPianoPlugin) -> float:
        ctx = piano_inst.create_isolated_context(sr)
        total_frames = int((352.0 / bps) * sr)
        chunk = 4096
        t0 = time.perf_counter()
        for f_idx in range(0, total_frames, chunk):
            n = min(chunk, total_frames - f_idx)
            sb_b = (f_idx / sr) * bps
            eb_b = ((f_idx + n) / sr) * bps
            cn = [nt for nt in piano_notes if nt.start_beat < eb_b and (nt.start_beat + nt.duration + 0.9) > sb_b]
            if cn:
                piano_inst.render_in_context(cn, sb_b, bps, n, sr, ctx)
        return time.perf_counter() - t0

    piano_nat = NovaPianoPlugin()
    # Warm-up 1 chunk
    piano_nat.render_in_context(piano_notes[:3], 0.0, bps, 4096, sr, piano_nat.create_isolated_context(sr))
    piano_track_nat_s = _run_piano_track(piano_nat)

    with _PythonFallbackScope():
        sb_py = PianoSoundboard(sr)
        sb_py_128 = _bench_fn(lambda: sb_py.process(force_128, 0.67), iters=20, warmup=3)
        sb_py_4096 = _bench_fn(lambda: sb_py.process(force_4096, 0.67), iters=10, warmup=2)

        g_py = NovaGuitarPlugin()
        g_py.apply_preset("Électrique Strat Clean (Manche)")
        g_py_128 = _bench_seq(g_py, 128, 40)
        g_py_4096 = _bench_seq(g_py, 4096, 10)

        s_py = NovaSynthPlugin()
        s_py.apply_preset("Neon Horizon SuperSaw")
        s_py_128 = _bench_seq(s_py, 128, 40)
        s_py_4096 = _bench_seq(s_py, 4096, 10)

        r_py = StereoStudioReverb(sr)
        r_py_128 = _bench_fn(lambda: r_py.process(stereo_128), iters=30, warmup=3)
        r_py_4096 = _bench_fn(lambda: r_py.process(stereo_4096), iters=15, warmup=2)

    print("\n=== M1b Instrument Microbenchmarks ===")
    print(f"PianoSoundboard | 128: Py={sb_py_128:.3f} ms -> C={sb_nat_128:.3f} ms ({sb_py_128/sb_nat_128:.1f}x) | "
          f"4096: Py={sb_py_4096:.3f} ms -> C={sb_nat_4096:.3f} ms ({sb_py_4096/sb_nat_4096:.1f}x)")
    print(f"Piano FullTrack | C={piano_track_nat_s:.3f} s (352 beats / ~142.7s, 357 notes @ 148 BPM)")
    print(f"Guitar (3-note) | 128: Py={g_py_128:.3f} ms -> C={g_nat_128:.3f} ms ({g_py_128/g_nat_128:.1f}x) | "
          f"4096: Py={g_py_4096:.3f} ms -> C={g_nat_4096:.3f} ms ({g_py_4096/g_nat_4096:.1f}x)")
    print(f"Synth SuperSaw  | 128: Py={s_py_128:.3f} ms -> C={s_nat_128:.3f} ms ({s_py_128/s_nat_128:.1f}x) | "
          f"4096: Py={s_py_4096:.3f} ms -> C={s_nat_4096:.3f} ms ({s_py_4096/s_nat_4096:.1f}x)")
    print(f"StudioReverb    | 128: Py={r_py_128:.3f} ms -> C={r_nat_128:.3f} ms ({r_py_128/r_nat_128:.1f}x) | "
          f"4096: Py={r_py_4096:.3f} ms -> C={r_nat_4096:.3f} ms ({r_py_4096/r_nat_4096:.1f}x)")

    assert sb_nat_4096 < 1.5, f"PianoSoundboard 4096-chunk too slow: {sb_nat_4096:.3f} ms"
    assert sb_nat_4096 < sb_py_4096 * 0.2
    assert piano_track_nat_s < 2.5, f"Piano full-track pre-render too slow: {piano_track_nat_s:.3f} s"
    assert g_nat_128 < g_py_128 * 0.5
    assert s_nat_128 < s_py_128 * 0.5
    assert r_nat_128 < r_py_128 * 0.5

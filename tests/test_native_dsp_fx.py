"""
tests/test_native_dsp_fx.py - Unit, parity, edge-case, fallback, and benchmark tests for M1a Native C99 DSP.
"""
import ctypes
import importlib
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pytest

from core import dsp_native
from plugins.compressor.compressor_plugin import CompressorPlugin
from plugins.equalizer.equalizer_plugin import EqualizerPlugin
from plugins.guitar_amp.amp_plugin import NovaGuitarAmpPlugin
from plugins.mixer.mixer_plugin import MixerPlugin


def _rmse(a: np.ndarray, b: np.ndarray) -> float:
    diff = a.astype(np.float64) - b.astype(np.float64)
    return float(np.sqrt(np.mean(diff * diff)))


def _make_test_signal(frames: int = 4096, sr: int = 44100, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(frames, dtype=np.float64) / float(sr)
    # Mix low frequency (50 Hz), mid (440 Hz), high (2500 Hz), and non-zero DC offset to exercise lfilter_zi
    l = 0.35 * np.sin(2.0 * np.pi * 110.0 * t) + 0.25 * np.sin(2.0 * np.pi * 880.0 * t) + 0.08 * rng.standard_normal(frames) + 0.15
    r = 0.30 * np.sin(2.0 * np.pi * 165.0 * t + 0.4) + 0.20 * np.sin(2.0 * np.pi * 2200.0 * t) + 0.08 * rng.standard_normal(frames) - 0.10
    return np.column_stack((l, r)).astype(np.float32)


def _run_chunks(plugin, audio: np.ndarray, sr: int, chunk_size: int) -> np.ndarray:
    parts = []
    for i in range(0, len(audio), chunk_size):
        parts.append(plugin.process(audio[i : i + chunk_size], sr))
    return np.vstack(parts) if parts else np.empty((0, 2), dtype=np.float32)


class _PythonFallbackScope:
    """Context manager to reload core.dsp_native with NOVADAW_DISABLE_NATIVE_DSP=1 and restore afterwards."""

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


# ---------------------------------------------------------------------------
# 1. Loader, Build Residue & Fallback Flag Tests
# ---------------------------------------------------------------------------


def test_native_loader_and_clean_bin_dir():
    assert dsp_native.NATIVE_AVAILABLE is True
    assert dsp_native.lib is not None
    assert dsp_native.nova_dsp_abi_version() == 1

    bin_dir = Path(dsp_native.DLL_PATH).parent
    assert dsp_native.DLL_PATH.is_file()
    residue = [
        p.name
        for p in bin_dir.iterdir()
        if p.name.startswith("nova_dsp") and p.suffix.lower() in (".obj", ".lib", ".exp")
    ]
    assert residue == [], f"Unexpected build artifacts in core/bin: {residue}"


def test_disable_native_dsp_env_flag_subprocess():
    env = os.environ.copy()
    env["NOVADAW_DISABLE_NATIVE_DSP"] = "1"
    code = "\n".join([
        "from core import dsp_native",
        "from plugins.guitar_amp.amp_plugin import NovaGuitarAmpPlugin",
        "from plugins.equalizer.equalizer_plugin import EqualizerPlugin",
        "from plugins.compressor.compressor_plugin import CompressorPlugin",
        "from plugins.mixer.mixer_plugin import MixerPlugin",
        "import numpy as np",
        "assert dsp_native.NATIVE_AVAILABLE is False",
        "x = np.ones((128, 2), dtype=np.float32) * 0.25",
        "for p in (NovaGuitarAmpPlugin(), EqualizerPlugin(), CompressorPlugin(), MixerPlugin()):",
        "    y = p.process(x, 44100)",
        "    assert y.shape == (128, 2) and np.all(np.isfinite(y))",
        "print('OK')",
    ])
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"Fallback subprocess failed:\n{proc.stderr}\n{proc.stdout}"
    assert "OK" in proc.stdout


# ---------------------------------------------------------------------------
# 2. Native vs Python Fallback Parity Tests (RMSE < 1e-4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "preset,sr,cab,chunk",
    [
        ("Clean Studio (Neutre)", 44100, True, 4096),
        ("Blues Crunch", 44100, True, 128),
        ("High Gain Metal", 48000, True, 512),
        ("Classic Rock Overdrive", 44100, False, 256),
    ],
)
def test_guitar_amp_native_vs_python_parity(preset, sr, cab, chunk):
    audio = _make_test_signal(4096, sr=sr, seed=101) * 0.6

    amp_nat = NovaGuitarAmpPlugin()
    assert amp_nat.apply_preset(preset)
    amp_nat.cab_enabled = cab
    y_nat = _run_chunks(amp_nat, audio, sr, chunk)
    pk_in_nat, pk_out_nat = amp_nat.current_input_peak_db, amp_nat.current_output_peak_db

    with _PythonFallbackScope():
        assert dsp_native.NATIVE_AVAILABLE is False
        amp_py = NovaGuitarAmpPlugin()
        assert amp_py._dsp._native is None
        assert amp_py.apply_preset(preset)
        amp_py.cab_enabled = cab
        y_py = _run_chunks(amp_py, audio, sr, chunk)
        pk_in_py, pk_out_py = amp_py.current_input_peak_db, amp_py.current_output_peak_db

    assert dsp_native.NATIVE_AVAILABLE is True
    assert _rmse(y_nat, y_py) < 1e-4
    assert abs(pk_in_nat - pk_in_py) < 1e-3
    assert abs(pk_out_nat - pk_out_py) < 1e-3


def test_equalizer_native_vs_python_parity_all_filter_types_and_bands():
    sr = 44100
    audio = _make_test_signal(4096, sr=sr, seed=202)

    def _configure(eq: EqualizerPlugin):
        eq.set_band_count(10)
        eq.bands[0].filter_type = "high_pass"
        eq.bands[0].frequency = 95.0
        eq.bands[0].q = 0.707
        eq.bands[1].filter_type = "low_shelf"
        eq.bands[1].frequency = 140.0
        eq.bands[1].gain_db = 4.5
        eq.bands[4].filter_type = "peaking"
        eq.bands[4].frequency = 750.0
        eq.bands[4].gain_db = -5.0
        eq.bands[4].q = 1.8
        eq.bands[7].filter_type = "high_shelf"
        eq.bands[7].frequency = 6000.0
        eq.bands[7].gain_db = 3.5
        eq.bands[9].filter_type = "low_pass"
        eq.bands[9].frequency = 9500.0
        eq.bands[9].q = 0.85
        eq.master_gain_db = -1.5

    eq_nat = EqualizerPlugin(band_count=10)
    _configure(eq_nat)
    y_nat = _run_chunks(eq_nat, audio, sr, 128)

    # Toggle band enabled mid-stream and verify parity
    eq_nat.bands[4].enabled = False
    y_nat_2 = _run_chunks(eq_nat, audio, sr, 512)

    with _PythonFallbackScope():
        eq_py = EqualizerPlugin(band_count=10)
        assert eq_py._native is None
        _configure(eq_py)
        y_py = _run_chunks(eq_py, audio, sr, 128)
        eq_py.bands[4].enabled = False
        y_py_2 = _run_chunks(eq_py, audio, sr, 512)

    assert _rmse(y_nat, y_py) < 1e-4
    assert _rmse(y_nat_2, y_py_2) < 1e-4


@pytest.mark.parametrize("band_count", [3, 12, 24])
def test_equalizer_band_counts_parity(band_count):
    sr = 48000
    audio = _make_test_signal(2048, sr=sr, seed=203)

    eq_nat = EqualizerPlugin(band_count=band_count)
    eq_nat.apply_preset("Vocal Clarity")
    y_nat = eq_nat.process(audio, sr)

    with _PythonFallbackScope():
        eq_py = EqualizerPlugin(band_count=band_count)
        eq_py.apply_preset("Vocal Clarity")
        y_py = eq_py.process(audio, sr)

    assert _rmse(y_nat, y_py) < 1e-4


@pytest.mark.parametrize(
    "mode,hpf,knee,auto_mk,mix,chunk",
    [
        ("peak", 0.0, 0.0, False, 1.0, 128),
        ("peak", 80.0, 4.0, True, 0.8, 512),
        ("rms", 0.0, 6.0, False, 1.0, 256),
        ("rms", 80.0, 2.5, True, 0.4, 128),
    ],
)
def test_compressor_native_vs_python_parity(mode, hpf, knee, auto_mk, mix, chunk):
    sr = 44100
    audio = _make_test_signal(4096, sr=sr, seed=303)

    kwargs = dict(
        threshold_db=-20.0,
        ratio=4.5,
        attack_ms=8.0,
        release_ms=90.0,
        knee_db=knee,
        makeup_gain_db=2.0,
        auto_makeup=auto_mk,
        mix=mix,
        detection_mode=mode,
        sidechain_hpf_hz=hpf,
    )
    c_nat = CompressorPlugin(**kwargs)
    y_nat = _run_chunks(c_nat, audio, sr, chunk)

    with _PythonFallbackScope():
        c_py = CompressorPlugin(**kwargs)
        assert c_py._c_state is None
        y_py = _run_chunks(c_py, audio, sr, chunk)

    assert _rmse(y_nat, y_py) < 1e-4
    assert abs(c_nat.current_gain_reduction_db - c_py.current_gain_reduction_db) < 1e-2
    assert abs(c_nat.current_input_peak_db - c_py.current_input_peak_db) < 1e-3
    assert abs(c_nat.current_output_peak_db - c_py.current_output_peak_db) < 1e-3


@pytest.mark.parametrize(
    "vol,pan,mono,dim",
    [
        (1.0, 0.0, False, False),
        (0.5, -0.7, False, False),
        (1.3, 0.6, True, False),
        (0.9, 1.0, False, True),
    ],
)
def test_mixer_native_vs_python_parity(vol, pan, mono, dim):
    sr = 44100
    audio = _make_test_signal(2048, sr=sr, seed=404)

    def _cfg(m: MixerPlugin):
        m.master_volume = vol
        m.master_pan = pan
        m.mono_switch = mono
        m.dim_switch = dim

    m_nat = MixerPlugin()
    _cfg(m_nat)
    y_nat = _run_chunks(m_nat, audio, sr, 256)

    with _PythonFallbackScope():
        m_py = MixerPlugin()
        _cfg(m_py)
        y_py = _run_chunks(m_py, audio, sr, 256)

    assert _rmse(y_nat, y_py) < 1e-4
    assert abs(m_nat.peak_left_db - m_py.peak_left_db) < 1e-4
    assert abs(m_nat.peak_right_db - m_py.peak_right_db) < 1e-4
    assert abs(m_nat.rms_left_db - m_py.rms_left_db) < 1e-4
    assert abs(m_nat.rms_right_db - m_py.rms_right_db) < 1e-4


# ---------------------------------------------------------------------------
# 3. State Serialization, Reset Determinism & Edge Cases
# ---------------------------------------------------------------------------


def test_state_roundtrip_and_reset_determinism():
    sr = 44100
    audio = _make_test_signal(1024, sr=sr, seed=505)

    for factory, mutator in [
        (NovaGuitarAmpPlugin, lambda p: p.apply_preset("High Gain Metal")),
        (EqualizerPlugin, lambda p: p.apply_preset("Vocal Clarity")),
        (CompressorPlugin, lambda p: p.apply_preset("Batterie Punch (VCA)")),
        (MixerPlugin, lambda p: p.set_state({"master_volume": 0.72, "master_pan": -0.35, "mono_switch": True, "dim_switch": True})),
    ]:
        p1 = factory()
        mutator(p1)
        out1 = p1.process(audio, sr)
        p1.reset()
        out2 = p1.process(audio, sr)
        assert np.allclose(out1, out2, atol=1e-7), f"{type(p1).__name__} reset() not deterministic"

        state = p1.get_state()
        p2 = factory()
        p2.set_state(state)
        out3 = p2.process(audio, sr)
        assert np.allclose(out1, out3, atol=1e-7), f"{type(p1).__name__} get_state/set_state mismatch"


def test_edge_cases_shapes_nan_bypass_immutability():
    sr = 44100
    plugins = [
        NovaGuitarAmpPlugin(distortion=0.65),
        EqualizerPlugin(band_count=10),
        CompressorPlugin(threshold_db=-20.0, ratio=4.0, sidechain_hpf_hz=80.0),
        MixerPlugin(master_volume=0.8, master_pan=-0.25),
    ]
    plugins[1].apply_preset("Bass Boost")

    for p in plugins:
        # 1. 0 frames
        empty = np.empty((0, 2), dtype=np.float32)
        out_empty = p.process(empty, sr)
        assert len(out_empty) == 0

        # 2. Silence
        silence = np.zeros((512, 2), dtype=np.float32)
        out_sil = p.process(silence, sr)
        assert out_sil.shape == (512, 2)
        assert np.all(np.isfinite(out_sil))
        # GuitarAmp has asymmetric grid bias offsets (0.05*dist) whose DC-blocker settles below 1e-4
        sil_tol = 1e-4 if isinstance(p, NovaGuitarAmpPlugin) else 1e-6
        assert np.max(np.abs(out_sil)) < sil_tol

        # 3. NaN / +Inf / -Inf sanitization + subsequent clean recovery
        dirty = _make_test_signal(256, sr=sr, seed=606)
        dirty[10, 0] = np.nan
        dirty[20, 1] = np.inf
        dirty[30, 0] = -np.inf
        out_dirty = p.process(dirty, sr)
        assert out_dirty.shape == (256, 2)
        assert np.all(np.isfinite(out_dirty))

        clean = _make_test_signal(256, sr=sr, seed=607)
        out_after = p.process(clean, sr)
        assert np.all(np.isfinite(out_after))

        # 4. 1D (N,), (N, 1), non-contiguous strided (N, 2), float64
        mono_1d = clean[:, 0].copy()
        assert p.process(mono_1d, sr).shape == (256, 2)

        mono_2d = clean[:, :1].copy()
        assert p.process(mono_2d, sr).shape == (256, 2)

        strided = _make_test_signal(512, sr=sr, seed=608)[::2]
        assert not strided.flags["C_CONTIGUOUS"]
        out_strided = p.process(strided, sr)
        assert out_strided.shape == (256, 2) and out_strided.flags["C_CONTIGUOUS"]

        f64 = clean.astype(np.float64)
        out_f64 = p.process(f64, sr)
        assert out_f64.dtype == np.float32 and out_f64.shape == (256, 2)

        # 5. Input immutability
        snapshot = clean.copy()
        _ = p.process(clean, sr)
        assert np.array_equal(clean, snapshot), f"{type(p).__name__} mutated caller input!"

        # 6. Bypass bit-identity
        p.enabled = False
        out_byp = p.process(clean, sr)
        assert np.allclose(out_byp, clean, atol=1e-7)
        p.enabled = True


def test_c_abi_aliasing_safety_comp_and_mixer():
    sr = 44100
    frames = 256
    src = _make_test_signal(frames, sr=sr, seed=707)

    # Compressor aliasing vs non-aliasing
    st_ref = dsp_native.NovaCompressorState()
    st_alias = dsp_native.NovaCompressorState()
    dsp_native.nova_comp_reset(ctypes.byref(st_ref))
    dsp_native.nova_comp_reset(ctypes.byref(st_alias))

    out_ref = np.empty_like(src)
    dsp_native.nova_comp_process(
        ctypes.byref(st_ref),
        src.ctypes.data_as(dsp_native.c_float_p),
        out_ref.ctypes.data_as(dsp_native.c_float_p),
        frames, sr, -20.0, 4.0, 10.0, 100.0, 4.0, 2.0, 1, 0.85, 1, 80.0, 1,
        None, None, None,
    )
    buf_alias = src.copy()
    dsp_native.nova_comp_process(
        ctypes.byref(st_alias),
        buf_alias.ctypes.data_as(dsp_native.c_float_p),
        buf_alias.ctypes.data_as(dsp_native.c_float_p),
        frames, sr, -20.0, 4.0, 10.0, 100.0, 4.0, 2.0, 1, 0.85, 1, 80.0, 1,
        None, None, None,
    )
    assert np.array_equal(out_ref, buf_alias)

    # Mixer aliasing vs non-aliasing
    out_m_ref = np.empty_like(src)
    dsp_native.nova_mixer_process(
        src.ctypes.data_as(dsp_native.c_float_p),
        out_m_ref.ctypes.data_as(dsp_native.c_float_p),
        frames, sr, 0.8, -0.3, 1, 1, 0, None, None, None, None,
    )
    buf_m_alias = src.copy()
    dsp_native.nova_mixer_process(
        buf_m_alias.ctypes.data_as(dsp_native.c_float_p),
        buf_m_alias.ctypes.data_as(dsp_native.c_float_p),
        frames, sr, 0.8, -0.3, 1, 1, 0, None, None, None, None,
    )
    assert np.array_equal(out_m_ref, buf_m_alias)


# ---------------------------------------------------------------------------
# 4. Microbenchmarks (128 and 1024 frames)
# ---------------------------------------------------------------------------


def _bench_plugin(plugin, audio: np.ndarray, sr: int = 44100, iters: int = 300, rounds: int = 3) -> float:
    for _ in range(15):
        plugin.process(audio, sr)
    best = float("inf")
    for _ in range(rounds):
        t0 = time.perf_counter()
        for _ in range(iters):
            plugin.process(audio, sr)
        elapsed_ms = ((time.perf_counter() - t0) / iters) * 1000.0
        if elapsed_ms < best:
            best = elapsed_ms
    return best


def test_microbenchmark_native_speedup():
    sr = 44100
    buf_128 = _make_test_signal(128, sr=sr, seed=808)
    buf_1024 = _make_test_signal(1024, sr=sr, seed=809)

    def _make_plugins():
        amp = NovaGuitarAmpPlugin()
        amp.apply_preset("High Gain Metal")
        eq = EqualizerPlugin(band_count=10)
        eq.apply_preset("Vocal Clarity")
        comp = CompressorPlugin(threshold_db=-18.0, ratio=4.0, sidechain_hpf_hz=80.0)
        mix = MixerPlugin(master_volume=0.85, master_pan=-0.2)
        return {"GuitarAmp": amp, "Equalizer": eq, "Compressor": comp, "Mixer": mix}

    native_plugins = _make_plugins()
    nat_128 = {k: _bench_plugin(p, buf_128, sr) for k, p in native_plugins.items()}
    nat_1024 = {k: _bench_plugin(p, buf_1024, sr) for k, p in native_plugins.items()}

    with _PythonFallbackScope():
        py_plugins = _make_plugins()
        py_128 = {k: _bench_plugin(p, buf_128, sr, iters=100) for k, p in py_plugins.items()}
        py_1024 = {k: _bench_plugin(p, buf_1024, sr, iters=100) for k, p in py_plugins.items()}

    print("\n=== M1a Microbenchmark (ms/block) ===")
    for name in native_plugins:
        spd_128 = py_128[name] / max(1e-6, nat_128[name])
        spd_1024 = py_1024[name] / max(1e-6, nat_1024[name])
        print(
            f"{name:12s} | 128 smp: Py={py_128[name]:.4f} ms -> C={nat_128[name]:.4f} ms ({spd_128:.1f}x) | "
            f"1024 smp: Py={py_1024[name]:.4f} ms -> C={nat_1024[name]:.4f} ms ({spd_1024:.1f}x)"
        )
        assert nat_128[name] < py_128[name]
        assert nat_1024[name] < py_1024[name]

    # Explorer 2 budget: Amp < 0.15 ms (target ~0.05 ms), EQ/Comp/Mixer < 0.05 ms @ 128 frames
    assert nat_128["GuitarAmp"] < 0.08, f"GuitarAmp 128-frame exceeded 0.08 ms guard: {nat_128['GuitarAmp']:.4f} ms"
    assert nat_128["Equalizer"] < 0.05
    assert nat_128["Compressor"] < 0.05
    assert nat_128["Mixer"] < 0.05

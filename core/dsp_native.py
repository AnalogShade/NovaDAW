"""
core/dsp_native.py - GIL-free ctypes loader and ABI bindings for NovaDAW native C99 DSP.

Honors environment variable NOVADAW_DISABLE_NATIVE_DSP ("1", "true", "yes", "on").
Falls back gracefully with NATIVE_AVAILABLE = False if disabled, missing, or ABI-mismatched.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Optional

EXPECTED_ABI_VERSION = 1
DLL_PATH = Path(__file__).resolve().parent / "bin" / "nova_dsp.dll"

c_float_p = ctypes.POINTER(ctypes.c_float)
c_double_p = ctypes.POINTER(ctypes.c_double)
c_int32_p = ctypes.POINTER(ctypes.c_int32)
c_uint64_p = ctypes.POINTER(ctypes.c_uint64)


class NovaCompressorState(ctypes.Structure):
    """Caller-owned POD state matching NovaCompressorState in core/bin/nova_dsp.c."""

    _fields_ = [
        ("env_peak", ctypes.c_double),
        ("env_gr_db", ctypes.c_double),
        ("hpf_z0", ctypes.c_double),
        ("hpf_z1", ctypes.c_double),
        ("cached_hpf_freq", ctypes.c_double),
        ("cached_hpf_sr", ctypes.c_int32),
        ("hpf_initialized", ctypes.c_int32),
        ("hpf_b0", ctypes.c_double),
        ("hpf_b1", ctypes.c_double),
        ("hpf_b2", ctypes.c_double),
        ("hpf_a1", ctypes.c_double),
        ("hpf_a2", ctypes.c_double),
    ]


class NovaBiquad(ctypes.Structure):
    """Matching NovaBiquad in core/bin/nova_dsp.c."""

    _fields_ = [
        ("b0", ctypes.c_double),
        ("b1", ctypes.c_double),
        ("b2", ctypes.c_double),
        ("a1", ctypes.c_double),
        ("a2", ctypes.c_double),
        ("z_l", ctypes.c_double * 2),
        ("z_r", ctypes.c_double * 2),
    ]


class NovaGuitarBodyState(ctypes.Structure):
    """Caller-owned POD state matching NovaGuitarBodyState in core/bin/nova_dsp.c."""

    _fields_ = [
        ("cached_mode", ctypes.c_int32),
        ("cached_sr", ctypes.c_int32),
        ("body_res_milli", ctypes.c_int32),
        ("pickup_pos_milli", ctypes.c_int32),
        ("initialized", ctypes.c_int32),
        ("pickup_pos_idx", ctypes.c_int32),
        ("pickup_buf", ctypes.c_double * 64),
        ("filters", NovaBiquad * 5),
        ("gains", ctypes.c_double * 4),
    ]


class NovaGuitarWidenerState(ctypes.Structure):
    """Caller-owned POD state matching NovaGuitarWidenerState in core/bin/nova_dsp.c."""

    _fields_ = [
        ("delay_z", ctypes.c_double),
        ("z_a", (ctypes.c_double * 2) * 4),
        ("z_b", (ctypes.c_double * 2) * 4),
    ]


class NovaSVFState(ctypes.Structure):
    """Caller-owned POD state matching NovaSVFState in core/bin/nova_dsp.c."""

    _fields_ = [
        ("prev_cutoff", ctypes.c_double),
        ("prev_q", ctypes.c_double),
        ("prev_type", ctypes.c_int32),
        ("prev_sr", ctypes.c_int32),
        ("initialized", ctypes.c_int32),
        ("has_prev", ctypes.c_int32),
        ("b0", ctypes.c_double),
        ("b1", ctypes.c_double),
        ("b2", ctypes.c_double),
        ("a1", ctypes.c_double),
        ("a2", ctypes.c_double),
        ("zi_l", ctypes.c_double * 2),
        ("zi_r", ctypes.c_double * 2),
        ("prev_x_l", ctypes.c_double * 2),
        ("prev_x_r", ctypes.c_double * 2),
        ("prev_y_l", ctypes.c_double * 2),
        ("prev_y_r", ctypes.c_double * 2),
    ]


def _is_native_disabled() -> bool:
    val = os.environ.get("NOVADAW_DISABLE_NATIVE_DSP", "").strip().lower()
    return val in ("1", "true", "yes", "on")


lib: Optional[ctypes.CDLL] = None
NATIVE_AVAILABLE: bool = False

# Exported C function handles (None when NATIVE_AVAILABLE is False)
nova_dsp_abi_version = None
nova_amp_create = None
nova_amp_destroy = None
nova_amp_reset = None
nova_amp_process = None
nova_eq_create = None
nova_eq_destroy = None
nova_eq_reset = None
nova_eq_set_num_bands = None
nova_eq_set_band = None
nova_eq_reset_band = None
nova_eq_process = None
nova_comp_reset = None
nova_comp_process = None
nova_mixer_process = None

# M1b: PianoSoundboard
nova_sb_create = None
nova_sb_destroy = None
nova_sb_reset = None
nova_sb_process = None
nova_piano_eval_positions = None

# M1b: Guitar
nova_guitar_plane_create = None
nova_guitar_plane_destroy = None
nova_guitar_plane_reset = None
nova_guitar_plane_step = None
nova_guitar_voice_render = None
nova_guitar_body_reset = None
nova_guitar_body_process = None
nova_guitar_widener_reset = None
nova_guitar_widener_process = None

# M1b: Synth & Shared Reverb
nova_synth_osc_process = None
nova_synth_adsr_process = None
nova_svf_reset = None
nova_svf_process = None
nova_delay_create = None
nova_delay_destroy = None
nova_delay_reset = None
nova_delay_process = None
nova_reverb_create = None
nova_reverb_destroy = None
nova_reverb_reset = None
nova_reverb_process = None
nova_synth_render_layer = None


def _bind_library() -> None:
    global lib, NATIVE_AVAILABLE
    global nova_dsp_abi_version
    global nova_amp_create, nova_amp_destroy, nova_amp_reset, nova_amp_process
    global nova_eq_create, nova_eq_destroy, nova_eq_reset, nova_eq_set_num_bands
    global nova_eq_set_band, nova_eq_reset_band, nova_eq_process
    global nova_comp_reset, nova_comp_process, nova_mixer_process
    global nova_sb_create, nova_sb_destroy, nova_sb_reset, nova_sb_process, nova_piano_eval_positions
    global nova_guitar_plane_create, nova_guitar_plane_destroy, nova_guitar_plane_reset
    global nova_guitar_plane_step, nova_guitar_voice_render
    global nova_guitar_body_reset, nova_guitar_body_process
    global nova_guitar_widener_reset, nova_guitar_widener_process
    global nova_synth_osc_process, nova_synth_adsr_process
    global nova_svf_reset, nova_svf_process
    global nova_delay_create, nova_delay_destroy, nova_delay_reset, nova_delay_process
    global nova_reverb_create, nova_reverb_destroy, nova_reverb_reset, nova_reverb_process
    global nova_synth_render_layer

    if _is_native_disabled() or not DLL_PATH.is_file():
        lib = None
        NATIVE_AVAILABLE = False
        return

    try:
        loaded = ctypes.CDLL(str(DLL_PATH))

        abi_fn = loaded.nova_dsp_abi_version
        abi_fn.argtypes = []
        abi_fn.restype = ctypes.c_int32
        if int(abi_fn()) != EXPECTED_ABI_VERSION:
            lib = None
            NATIVE_AVAILABLE = False
            return

        # GuitarAmp
        amp_create = loaded.nova_amp_create
        amp_create.argtypes = [ctypes.c_int32]
        amp_create.restype = ctypes.c_void_p

        amp_destroy = loaded.nova_amp_destroy
        amp_destroy.argtypes = [ctypes.c_void_p]
        amp_destroy.restype = None

        amp_reset = loaded.nova_amp_reset
        amp_reset.argtypes = [ctypes.c_void_p]
        amp_reset.restype = None

        amp_process = loaded.nova_amp_process
        amp_process.argtypes = [
            ctypes.c_void_p,  # ctx
            c_float_p,        # in_interleaved
            c_float_p,        # out_interleaved
            ctypes.c_int32,   # frames
            ctypes.c_int32,   # sample_rate
            ctypes.c_double,  # distortion
            ctypes.c_double,  # low_db
            ctypes.c_double,  # mid_db
            ctypes.c_double,  # treble_db
            ctypes.c_double,  # volume
            ctypes.c_int32,   # cab_enabled
            c_float_p,        # out_in_peak_db (nullable)
            c_float_p,        # out_out_peak_db (nullable)
        ]
        amp_process.restype = None

        # Equalizer
        eq_create = loaded.nova_eq_create
        eq_create.argtypes = [ctypes.c_int32]
        eq_create.restype = ctypes.c_void_p

        eq_destroy = loaded.nova_eq_destroy
        eq_destroy.argtypes = [ctypes.c_void_p]
        eq_destroy.restype = None

        eq_reset = loaded.nova_eq_reset
        eq_reset.argtypes = [ctypes.c_void_p]
        eq_reset.restype = None

        eq_set_num_bands = loaded.nova_eq_set_num_bands
        eq_set_num_bands.argtypes = [ctypes.c_void_p, ctypes.c_int32]
        eq_set_num_bands.restype = None

        eq_set_band = loaded.nova_eq_set_band
        eq_set_band.argtypes = [
            ctypes.c_void_p,  # ctx
            ctypes.c_int32,   # band_idx
            ctypes.c_double,  # b0
            ctypes.c_double,  # b1
            ctypes.c_double,  # b2
            ctypes.c_double,  # a1
            ctypes.c_double,  # a2
            ctypes.c_int32,   # active
            ctypes.c_int32,   # reset_zi
        ]
        eq_set_band.restype = None

        eq_reset_band = loaded.nova_eq_reset_band
        eq_reset_band.argtypes = [ctypes.c_void_p, ctypes.c_int32]
        eq_reset_band.restype = None

        eq_process = loaded.nova_eq_process
        eq_process.argtypes = [
            ctypes.c_void_p,  # ctx
            c_float_p,        # in_interleaved
            c_float_p,        # out_interleaved
            ctypes.c_int32,   # frames
            ctypes.c_double,  # master_gain_lin
        ]
        eq_process.restype = None

        # Compressor
        comp_state_p = ctypes.POINTER(NovaCompressorState)

        comp_reset = loaded.nova_comp_reset
        comp_reset.argtypes = [comp_state_p]
        comp_reset.restype = None

        comp_process = loaded.nova_comp_process
        comp_process.argtypes = [
            comp_state_p,     # st
            c_float_p,        # in_stereo
            c_float_p,        # out_stereo
            ctypes.c_int32,   # frames
            ctypes.c_int32,   # sample_rate
            ctypes.c_float,   # threshold_db
            ctypes.c_float,   # ratio
            ctypes.c_float,   # attack_ms
            ctypes.c_float,   # release_ms
            ctypes.c_float,   # knee_db
            ctypes.c_float,   # makeup_gain_db
            ctypes.c_int32,   # auto_makeup
            ctypes.c_float,   # mix
            ctypes.c_int32,   # detection_mode
            ctypes.c_float,   # sidechain_hpf_hz
            ctypes.c_int32,   # enabled
            c_float_p,        # out_in_peak_db (nullable)
            c_float_p,        # out_out_peak_db (nullable)
            c_float_p,        # out_gr_db (nullable)
        ]
        comp_process.restype = None

        # Mixer
        mixer_process = loaded.nova_mixer_process
        mixer_process.argtypes = [
            c_float_p,        # in_stereo
            c_float_p,        # out_stereo
            ctypes.c_int32,   # frames
            ctypes.c_int32,   # sample_rate
            ctypes.c_float,   # master_volume
            ctypes.c_float,   # master_pan
            ctypes.c_int32,   # mono_switch
            ctypes.c_int32,   # dim_switch
            ctypes.c_int32,   # soft_limit
            c_float_p,        # io_peak_left_db
            c_float_p,        # io_peak_right_db
            c_float_p,        # out_rms_left_db
            c_float_p,        # out_rms_right_db
        ]
        mixer_process.restype = None

        # PianoSoundboard
        sb_create = loaded.nova_sb_create
        sb_create.argtypes = [
            ctypes.c_int32,  # sample_rate
            ctypes.c_int32,  # num_modes
            c_double_p,      # plate_hz
            c_double_p,      # bridge_modes
            c_double_p,      # mode_pressure
            c_double_p,      # rad_sos
            c_double_p,      # symp_bp_sos
            c_double_p,      # body_lp_sos
            ctypes.c_int32,  # num_ap_l
            c_double_p,      # ap_l_sos
            ctypes.c_int32,  # num_ap_r
            c_double_p,      # ap_r_sos
            c_int32_p,       # tap_delays
            ctypes.c_int32,  # max_delay
            c_int32_p,       # wg_delays
        ]
        sb_create.restype = ctypes.c_void_p

        sb_destroy = loaded.nova_sb_destroy
        sb_destroy.argtypes = [ctypes.c_void_p]
        sb_destroy.restype = None

        sb_reset = loaded.nova_sb_reset
        sb_reset.argtypes = [ctypes.c_void_p]
        sb_reset.restype = None

        sb_process = loaded.nova_sb_process
        sb_process.argtypes = [
            ctypes.c_void_p,  # ctx
            c_float_p,        # in_5ch
            c_float_p,        # out_stereo
            ctypes.c_int32,   # frames
            ctypes.c_double,  # resonance
        ]
        sb_process.restype = None

        piano_eval_positions = loaded.nova_piano_eval_positions
        piano_eval_positions.argtypes = [
            c_double_p,      # poles_c128
            c_double_p,      # state_c128
            ctypes.c_int32,  # num_modes
            ctypes.c_int32,  # n_blocks
            ctypes.c_double, # elapsed_sec
            ctypes.c_double, # block_dt_sec
            c_float_p,       # out_positions_c64
        ]
        piano_eval_positions.restype = None

        # Guitar
        g_plane_create = loaded.nova_guitar_plane_create
        g_plane_create.argtypes = [
            ctypes.c_int32,  # n_int
            c_double_p,      # b_sus3
            c_double_p,      # a_sus4
            c_double_p,      # b_rel3
            c_double_p,      # a_rel4
        ]
        g_plane_create.restype = ctypes.c_void_p

        g_plane_destroy = loaded.nova_guitar_plane_destroy
        g_plane_destroy.argtypes = [ctypes.c_void_p]
        g_plane_destroy.restype = None

        g_plane_reset = loaded.nova_guitar_plane_reset
        g_plane_reset.argtypes = [ctypes.c_void_p]
        g_plane_reset.restype = None

        g_plane_step = loaded.nova_guitar_plane_step
        g_plane_step.argtypes = [
            ctypes.c_void_p,  # ctx
            c_double_p,       # exc_in
            c_double_p,       # out_mono
            ctypes.c_int32,   # frames
            ctypes.c_int32,   # is_released
        ]
        g_plane_step.restype = None

        g_voice_render = loaded.nova_guitar_voice_render
        g_voice_render.argtypes = [
            ctypes.c_void_p,  # plane_a
            ctypes.c_void_p,  # plane_b (nullable)
            c_double_p,       # exc_buf
            ctypes.c_int32,   # exc_len
            ctypes.c_int32,   # start_sample
            ctypes.c_int32,   # note_dur_samples
            c_double_p,       # out_mono (nullable)
            ctypes.c_int32,   # frames
        ]
        g_voice_render.restype = None

        g_body_state_p = ctypes.POINTER(NovaGuitarBodyState)
        g_body_reset = loaded.nova_guitar_body_reset
        g_body_reset.argtypes = [g_body_state_p]
        g_body_reset.restype = None

        g_body_process = loaded.nova_guitar_body_process
        g_body_process.argtypes = [
            g_body_state_p,   # st
            c_double_p,       # in_mono
            c_double_p,       # out_mono
            ctypes.c_int32,   # frames
            ctypes.c_int32,   # mode
            ctypes.c_double,  # body_resonance
            ctypes.c_double,  # pickup_position
            ctypes.c_int32,   # sample_rate
        ]
        g_body_process.restype = None

        g_wid_state_p = ctypes.POINTER(NovaGuitarWidenerState)
        g_wid_reset = loaded.nova_guitar_widener_reset
        g_wid_reset.argtypes = [g_wid_state_p]
        g_wid_reset.restype = None

        g_wid_process = loaded.nova_guitar_widener_process
        g_wid_process.argtypes = [
            g_wid_state_p,    # st
            c_double_p,       # in_mono
            c_double_p,       # out_l
            c_double_p,       # out_r
            ctypes.c_int32,   # frames
            ctypes.c_double,  # stereo_width
        ]
        g_wid_process.restype = None

        # Synth & Shared Reverb
        s_osc_process = loaded.nova_synth_osc_process
        s_osc_process.argtypes = [
            ctypes.c_int32,   # wave_id
            ctypes.c_double,  # freq
            ctypes.c_int32,   # frames
            ctypes.c_int32,   # sample_rate
            ctypes.c_double,  # phase_offset
            ctypes.c_double,  # pulse_width
            ctypes.c_double,  # fm_ratio
            ctypes.c_double,  # fm_depth
            ctypes.c_double,  # unison_detune
            ctypes.c_double,  # unison_spread
            ctypes.c_double,  # time_offset
            c_double_p,       # t_vec_opt (nullable)
            c_uint64_p,       # rng_state (nullable)
            c_float_p,        # out_stereo
        ]
        s_osc_process.restype = None

        s_adsr_process = loaded.nova_synth_adsr_process
        s_adsr_process.argtypes = [
            ctypes.c_double,  # t_start
            ctypes.c_double,  # t_end
            ctypes.c_int32,   # frames
            ctypes.c_double,  # note_off_time
            ctypes.c_double,  # attack
            ctypes.c_double,  # decay
            ctypes.c_double,  # sustain
            ctypes.c_double,  # release
            c_float_p,        # out_env
        ]
        s_adsr_process.restype = None

        svf_state_p = ctypes.POINTER(NovaSVFState)
        svf_reset = loaded.nova_svf_reset
        svf_reset.argtypes = [svf_state_p]
        svf_reset.restype = None

        svf_process = loaded.nova_svf_process
        svf_process.argtypes = [
            svf_state_p,      # st
            c_float_p,        # in_stereo
            c_float_p,        # out_stereo
            ctypes.c_int32,   # frames
            ctypes.c_int32,   # sample_rate
            ctypes.c_int32,   # filter_type
            ctypes.c_double,  # cutoff
            ctypes.c_double,  # resonance
            ctypes.c_double,  # drive
            c_float_p,        # mod_cutoff (nullable)
        ]
        svf_process.restype = None

        delay_create = loaded.nova_delay_create
        delay_create.argtypes = [ctypes.c_int32]
        delay_create.restype = ctypes.c_void_p

        delay_destroy = loaded.nova_delay_destroy
        delay_destroy.argtypes = [ctypes.c_void_p]
        delay_destroy.restype = None

        delay_reset = loaded.nova_delay_reset
        delay_reset.argtypes = [ctypes.c_void_p]
        delay_reset.restype = None

        delay_process = loaded.nova_delay_process
        delay_process.argtypes = [
            ctypes.c_void_p,  # ctx
            c_float_p,        # in_stereo
            c_float_p,        # out_stereo
            ctypes.c_int32,   # frames
            ctypes.c_double,  # time_sec
            ctypes.c_double,  # feedback
            ctypes.c_int32,   # ping_pong
            ctypes.c_double,  # mix
            ctypes.c_int32,   # enabled
        ]
        delay_process.restype = None

        reverb_create = loaded.nova_reverb_create
        reverb_create.argtypes = [ctypes.c_int32]
        reverb_create.restype = ctypes.c_void_p

        reverb_destroy = loaded.nova_reverb_destroy
        reverb_destroy.argtypes = [ctypes.c_void_p]
        reverb_destroy.restype = None

        reverb_reset = loaded.nova_reverb_reset
        reverb_reset.argtypes = [ctypes.c_void_p]
        reverb_reset.restype = None

        reverb_process = loaded.nova_reverb_process
        reverb_process.argtypes = [
            ctypes.c_void_p,  # ctx
            c_float_p,        # in_stereo
            c_float_p,        # out_stereo
            ctypes.c_int32,   # frames
            ctypes.c_double,  # room_size
            ctypes.c_double,  # damping
            ctypes.c_double,  # mix
            ctypes.c_int32,   # enabled
        ]
        reverb_process.restype = None

        s_render_layer = loaded.nova_synth_render_layer
        s_render_layer.argtypes = [
            svf_state_p,      # svf
            c_float_p,        # bus_stereo
            ctypes.c_int32,   # frames
            ctypes.c_int32,   # sample_rate
            ctypes.c_double,  # t_start_rel
            ctypes.c_double,  # t_end_rel
            ctypes.c_double,  # note_off_rel
            ctypes.c_double,  # attack
            ctypes.c_double,  # decay
            ctypes.c_double,  # sustain
            ctypes.c_double,  # release
            ctypes.c_double,  # volume
            ctypes.c_double,  # vel
            ctypes.c_double,  # pan
            ctypes.c_double,  # freq
            ctypes.c_int32,   # wave_id
            ctypes.c_double,  # pulse_width
            ctypes.c_double,  # fm_ratio
            ctypes.c_double,  # fm_depth
            ctypes.c_double,  # unison_detune
            ctypes.c_double,  # unison_spread
            ctypes.c_double,  # filter_env_amount
            ctypes.c_double,  # f_attack
            ctypes.c_double,  # f_decay
            ctypes.c_double,  # f_sustain
            ctypes.c_double,  # f_release
            ctypes.c_int32,   # lfo_enabled
            ctypes.c_int32,   # lfo_wave_id
            ctypes.c_double,  # lfo_rate
            ctypes.c_double,  # lfo_depth
            ctypes.c_int32,   # lfo_target
            ctypes.c_double,  # slice_start_sec
            ctypes.c_double,  # slice_end_sec
            ctypes.c_int32,   # filter_type
            ctypes.c_double,  # cutoff
            ctypes.c_double,  # resonance
            ctypes.c_double,  # drive
        ]
        s_render_layer.restype = ctypes.c_int32

        lib = loaded
        nova_dsp_abi_version = abi_fn
        nova_amp_create = amp_create
        nova_amp_destroy = amp_destroy
        nova_amp_reset = amp_reset
        nova_amp_process = amp_process
        nova_eq_create = eq_create
        nova_eq_destroy = eq_destroy
        nova_eq_reset = eq_reset
        nova_eq_set_num_bands = eq_set_num_bands
        nova_eq_set_band = eq_set_band
        nova_eq_reset_band = eq_reset_band
        nova_eq_process = eq_process
        nova_comp_reset = comp_reset
        nova_comp_process = comp_process
        nova_mixer_process = mixer_process

        nova_sb_create = sb_create
        nova_sb_destroy = sb_destroy
        nova_sb_reset = sb_reset
        nova_sb_process = sb_process
        nova_piano_eval_positions = piano_eval_positions

        nova_guitar_plane_create = g_plane_create
        nova_guitar_plane_destroy = g_plane_destroy
        nova_guitar_plane_reset = g_plane_reset
        nova_guitar_plane_step = g_plane_step
        nova_guitar_voice_render = g_voice_render
        nova_guitar_body_reset = g_body_reset
        nova_guitar_body_process = g_body_process
        nova_guitar_widener_reset = g_wid_reset
        nova_guitar_widener_process = g_wid_process

        nova_synth_osc_process = s_osc_process
        nova_synth_adsr_process = s_adsr_process
        nova_svf_reset = svf_reset
        nova_svf_process = svf_process
        nova_delay_create = delay_create
        nova_delay_destroy = delay_destroy
        nova_delay_reset = delay_reset
        nova_delay_process = delay_process
        nova_reverb_create = reverb_create
        nova_reverb_destroy = reverb_destroy
        nova_reverb_reset = reverb_reset
        nova_reverb_process = reverb_process
        nova_synth_render_layer = s_render_layer

        NATIVE_AVAILABLE = True
    except Exception:
        lib = None
        NATIVE_AVAILABLE = False


_bind_library()

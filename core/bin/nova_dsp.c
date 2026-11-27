/*
 * core/bin/nova_dsp.c - Native C99/C11 DSP Kernels for NovaDAW
 *
 * MIT License
 *
 * Copyright (c) 2026 NovaDAW Contributors
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

#include <math.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#ifdef _WIN32
#define NOVA_API __declspec(dllexport)
#define NOVA_CALL __cdecl
#else
#define NOVA_API __attribute__((visibility("default")))
#define NOVA_CALL
#endif

#define NOVA_DSP_ABI_VERSION 1
#define NOVA_EQ_MAX_BANDS 32

/* --------------------------------------------------------------------------
 * ABI Version & Scalar Helpers (Zero Global Mutable State)
 * -------------------------------------------------------------------------- */

NOVA_API int32_t NOVA_CALL nova_dsp_abi_version(void) {
    return NOVA_DSP_ABI_VERSION;
}

static double nova_clamp_d(double x, double lo, double hi) {
    if (x < lo) return lo;
    if (x > hi) return hi;
    return x;
}

static double nova_sanitize_d(double x) {
    return isfinite(x) ? x : 0.0;
}

static double nova_flush_denormal_d(double x) {
    if (!isfinite(x) || fabs(x) < 1e-24) {
        return 0.0;
    }
    return x;
}

static float nova_sanitize_f32_out(double x) {
    if (!isfinite(x) || fabs(x) < 1e-30) {
        return 0.0f;
    }
    return (float)x;
}

static double nova_fast_tanh(double x) {
    double u, pv, qv;
    if (x > 5.0) {
        if (x > 8.0) return 1.0;
        {
            double e = exp(-2.0 * x);
            return (1.0 - e) / (1.0 + e);
        }
    }
    if (x < -5.0) {
        if (x < -8.0) return -1.0;
        {
            double e = exp(2.0 * x);
            return -(1.0 - e) / (1.0 + e);
        }
    }
    u = x * x;
    pv = 654729075.0 + u * (91891800.0 + u * (2837835.0 + u * (25740.0 + u * 55.0)));
    qv = 654729075.0 + u * (310134825.0 + u * (18918900.0 + u * (315315.0 + u * (1485.0 + u))));
    return x * pv / qv;
}

/* --------------------------------------------------------------------------
 * Shared Stereo Biquad DF-II-T Building Blocks
 * -------------------------------------------------------------------------- */

typedef struct NovaBiquad {
    double b0, b1, b2, a1, a2;
    double z_l[2];
    double z_r[2];
} NovaBiquad;

static void nova_biquad_reset(NovaBiquad* bq) {
    bq->z_l[0] = 0.0;
    bq->z_l[1] = 0.0;
    bq->z_r[0] = 0.0;
    bq->z_r[1] = 0.0;
}

static void nova_biquad_flush(NovaBiquad* bq) {
    bq->z_l[0] = nova_flush_denormal_d(bq->z_l[0]);
    bq->z_l[1] = nova_flush_denormal_d(bq->z_l[1]);
    bq->z_r[0] = nova_flush_denormal_d(bq->z_r[0]);
    bq->z_r[1] = nova_flush_denormal_d(bq->z_r[1]);
}

static double nova_biquad_step_l(NovaBiquad* bq, double x) {
    double y = bq->b0 * x + bq->z_l[0];
    bq->z_l[0] = bq->b1 * x - bq->a1 * y + bq->z_l[1];
    bq->z_l[1] = bq->b2 * x - bq->a2 * y;
    return y;
}

static double nova_biquad_step_r(NovaBiquad* bq, double x) {
    double y = bq->b0 * x + bq->z_r[0];
    bq->z_r[0] = bq->b1 * x - bq->a1 * y + bq->z_r[1];
    bq->z_r[1] = bq->b2 * x - bq->a2 * y;
    return y;
}

static void nova_biquad_set_peaking(NovaBiquad* bq, double freq, double gain_db, double q, int32_t sr) {
    double f = nova_clamp_d(freq, 20.0, (double)sr * 0.45);
    double a_lin = pow(10.0, gain_db / 40.0);
    double w0 = 2.0 * M_PI * f / (double)sr;
    double cos_w0 = cos(w0);
    double sin_w0 = sin(w0);
    double q_c = (q < 0.1) ? 0.1 : q;
    double alpha = sin_w0 / (2.0 * q_c);

    double b0 = 1.0 + alpha * a_lin;
    double b1 = -2.0 * cos_w0;
    double b2 = 1.0 - alpha * a_lin;
    double a0 = 1.0 + alpha / a_lin;
    double a1 = -2.0 * cos_w0;
    double a2 = 1.0 - alpha / a_lin;

    bq->b0 = b0 / a0;
    bq->b1 = b1 / a0;
    bq->b2 = b2 / a0;
    bq->a1 = a1 / a0;
    bq->a2 = a2 / a0;
}

static void nova_biquad_set_lowpass(NovaBiquad* bq, double freq, double q, int32_t sr) {
    double f = nova_clamp_d(freq, 20.0, (double)sr * 0.45);
    double w0 = 2.0 * M_PI * f / (double)sr;
    double cos_w0 = cos(w0);
    double sin_w0 = sin(w0);
    double q_c = (q < 0.2) ? 0.2 : q;
    double alpha = sin_w0 / (2.0 * q_c);

    double b0 = (1.0 - cos_w0) * 0.5;
    double b1 = 1.0 - cos_w0;
    double b2 = (1.0 - cos_w0) * 0.5;
    double a0 = 1.0 + alpha;
    double a1 = -2.0 * cos_w0;
    double a2 = 1.0 - alpha;

    bq->b0 = b0 / a0;
    bq->b1 = b1 / a0;
    bq->b2 = b2 / a0;
    bq->a1 = a1 / a0;
    bq->a2 = a2 / a0;
}

static void nova_biquad_set_highpass(NovaBiquad* bq, double freq, double q, int32_t sr) {
    double f = nova_clamp_d(freq, 15.0, (double)sr * 0.45);
    double w0 = 2.0 * M_PI * f / (double)sr;
    double cos_w0 = cos(w0);
    double sin_w0 = sin(w0);
    double q_c = (q < 0.2) ? 0.2 : q;
    double alpha = sin_w0 / (2.0 * q_c);

    double b0 = (1.0 + cos_w0) * 0.5;
    double b1 = -(1.0 + cos_w0);
    double b2 = (1.0 + cos_w0) * 0.5;
    double a0 = 1.0 + alpha;
    double a1 = -2.0 * cos_w0;
    double a2 = 1.0 - alpha;

    bq->b0 = b0 / a0;
    bq->b1 = b1 / a0;
    bq->b2 = b2 / a0;
    bq->a1 = a1 / a0;
    bq->a2 = a2 / a0;
}

static void nova_biquad_set_lowshelf(NovaBiquad* bq, double freq, double gain_db, double q, int32_t sr) {
    double f = nova_clamp_d(freq, 20.0, (double)sr * 0.45);
    double a_lin = pow(10.0, gain_db / 40.0);
    double w0 = 2.0 * M_PI * f / (double)sr;
    double cos_w0 = cos(w0);
    double sin_w0 = sin(w0);
    double q_c = (q < 0.2) ? 0.2 : q;
    double alpha = sin_w0 / (2.0 * q_c);
    double sqrt_a = sqrt(a_lin);
    double two_sqrt_a_alpha = 2.0 * sqrt_a * alpha;

    double b0 = a_lin * ((a_lin + 1.0) - (a_lin - 1.0) * cos_w0 + two_sqrt_a_alpha);
    double b1 = 2.0 * a_lin * ((a_lin - 1.0) - (a_lin + 1.0) * cos_w0);
    double b2 = a_lin * ((a_lin + 1.0) - (a_lin - 1.0) * cos_w0 - two_sqrt_a_alpha);
    double a0 = (a_lin + 1.0) + (a_lin - 1.0) * cos_w0 + two_sqrt_a_alpha;
    double a1 = -2.0 * ((a_lin - 1.0) + (a_lin + 1.0) * cos_w0);
    double a2 = (a_lin + 1.0) + (a_lin - 1.0) * cos_w0 - two_sqrt_a_alpha;

    bq->b0 = b0 / a0;
    bq->b1 = b1 / a0;
    bq->b2 = b2 / a0;
    bq->a1 = a1 / a0;
    bq->a2 = a2 / a0;
}

static void nova_biquad_set_highshelf(NovaBiquad* bq, double freq, double gain_db, double q, int32_t sr) {
    double f = nova_clamp_d(freq, 20.0, (double)sr * 0.45);
    double a_lin = pow(10.0, gain_db / 40.0);
    double w0 = 2.0 * M_PI * f / (double)sr;
    double cos_w0 = cos(w0);
    double sin_w0 = sin(w0);
    double q_c = (q < 0.2) ? 0.2 : q;
    double alpha = sin_w0 / (2.0 * q_c);
    double sqrt_a = sqrt(a_lin);
    double two_sqrt_a_alpha = 2.0 * sqrt_a * alpha;

    double b0 = a_lin * ((a_lin + 1.0) + (a_lin - 1.0) * cos_w0 + two_sqrt_a_alpha);
    double b1 = -2.0 * a_lin * ((a_lin - 1.0) + (a_lin + 1.0) * cos_w0);
    double b2 = a_lin * ((a_lin + 1.0) + (a_lin - 1.0) * cos_w0 - two_sqrt_a_alpha);
    double a0 = (a_lin + 1.0) - (a_lin - 1.0) * cos_w0 + two_sqrt_a_alpha;
    double a1 = 2.0 * ((a_lin - 1.0) - (a_lin + 1.0) * cos_w0);
    double a2 = (a_lin + 1.0) - (a_lin - 1.0) * cos_w0 - two_sqrt_a_alpha;

    bq->b0 = b0 / a0;
    bq->b1 = b1 / a0;
    bq->b2 = b2 / a0;
    bq->a1 = a1 / a0;
    bq->a2 = a2 / a0;
}

static void nova_onepole_set_lowpass(NovaBiquad* bq, double freq, int32_t sr) {
    double fc = nova_clamp_d(freq, 5.0, (double)sr * 0.45);
    double p = exp(-2.0 * M_PI * fc / (double)sr);
    bq->b0 = 1.0 - p;
    bq->b1 = 0.0;
    bq->b2 = 0.0;
    bq->a1 = -p;
    bq->a2 = 0.0;
}

static void nova_onepole_set_highpass(NovaBiquad* bq, double freq, int32_t sr) {
    double fc = nova_clamp_d(freq, 5.0, (double)sr * 0.45);
    double p = exp(-2.0 * M_PI * fc / (double)sr);
    double g = 0.5 * (1.0 + p);
    bq->b0 = g;
    bq->b1 = -g;
    bq->b2 = 0.0;
    bq->a1 = -p;
    bq->a2 = 0.0;
}

static void nova_biquad_set_bandpass(NovaBiquad* bq, double freq, double q, int32_t sr) {
    double f = nova_clamp_d(freq, 20.0, (double)sr * 0.45);
    double w0 = 2.0 * M_PI * f / (double)sr;
    double cos_w0 = cos(w0);
    double sin_w0 = sin(w0);
    double q_c = (q < 0.2) ? 0.2 : q;
    double alpha = sin_w0 / (2.0 * q_c);

    double b0 = alpha;
    double b1 = 0.0;
    double b2 = -alpha;
    double a0 = 1.0 + alpha;
    double a1 = -2.0 * cos_w0;
    double a2 = 1.0 - alpha;

    bq->b0 = b0 / a0;
    bq->b1 = b1 / a0;
    bq->b2 = b2 / a0;
    bq->a1 = a1 / a0;
    bq->a2 = a2 / a0;
}

/* --------------------------------------------------------------------------
 * 1. GuitarAmp Kernel (Fused 4x Oversampled 3-Stage Triode + Tone + Cab)
 * -------------------------------------------------------------------------- */

typedef struct NovaGuitarAmp {
    int32_t sr;
    int32_t dist_active;
    int32_t tight_valid;
    int32_t tone_valid;
    double last_dist;
    double last_low_db;
    double last_mid_db;
    double last_treble_db;

    double prev_sample[2];
    double env_alpha;
    double env_in2;
    double env_out2;

    NovaBiquad pre[1];
    NovaBiquad tight[2];
    NovaBiquad sag1[1];
    NovaBiquad sag2[1];
    NovaBiquad sag3[1];
    NovaBiquad inter1[2];
    NovaBiquad inter2[2];
    NovaBiquad aa[2];
    NovaBiquad dc[1];
    NovaBiquad tone[3];
    NovaBiquad cab[6];
} NovaGuitarAmp;

static double nova_triode_asym(double u, double pos_lim, double neg_lim) {
    double lim = (u >= 0.0) ? pos_lim : neg_lim;
    double s = u / lim;
    return u / sqrt(1.0 + s * s);
}

static void nova_amp_reset_dist_states(NovaGuitarAmp* ctx) {
    int i;
    for (i = 0; i < 2; ++i) {
        nova_biquad_reset(&ctx->tight[i]);
        nova_biquad_reset(&ctx->inter1[i]);
        nova_biquad_reset(&ctx->inter2[i]);
        nova_biquad_reset(&ctx->aa[i]);
    }
    nova_biquad_reset(&ctx->sag1[0]);
    nova_biquad_reset(&ctx->sag2[0]);
    nova_biquad_reset(&ctx->sag3[0]);
    nova_biquad_reset(&ctx->dc[0]);
    ctx->env_in2 = 0.0;
    ctx->env_out2 = 0.0;
    ctx->dist_active = 0;
}

static void nova_amp_ensure_static_filters(NovaGuitarAmp* ctx, int32_t sr) {
    int32_t sr4;
    double p_sag, fc_aa;
    int i;

    if (ctx->sr == sr) {
        return;
    }
    ctx->sr = sr;
    sr4 = sr * 4;

    /* 1. Pre-HPF 70 Hz */
    nova_biquad_set_highpass(&ctx->pre[0], 70.0, 0.707, sr);
    nova_biquad_reset(&ctx->pre[0]);

    /* 2. Interstage coupling @ 4x */
    nova_onepole_set_highpass(&ctx->inter1[0], 55.0, sr4);
    nova_onepole_set_lowpass(&ctx->inter1[1], 7200.0, sr4);
    ctx->inter2[0] = ctx->inter1[0];
    ctx->inter2[1] = ctx->inter1[1];
    nova_biquad_reset(&ctx->inter1[0]);
    nova_biquad_reset(&ctx->inter1[1]);
    nova_biquad_reset(&ctx->inter2[0]);
    nova_biquad_reset(&ctx->inter2[1]);

    /* Dynamic bias sag ~25 ms @ 4x */
    p_sag = exp(-1.0 / (0.025 * (double)sr4));
    ctx->sag1[0].b0 = 1.0 - p_sag;
    ctx->sag1[0].b1 = 0.0;
    ctx->sag1[0].b2 = 0.0;
    ctx->sag1[0].a1 = -p_sag;
    ctx->sag1[0].a2 = 0.0;
    ctx->sag2[0] = ctx->sag1[0];
    ctx->sag3[0] = ctx->sag1[0];
    nova_biquad_reset(&ctx->sag1[0]);
    nova_biquad_reset(&ctx->sag2[0]);
    nova_biquad_reset(&ctx->sag3[0]);

    /* 3. Anti-aliasing 4th-order Butterworth LP @ 4x */
    fc_aa = (0.42 * (double)sr < 18000.0) ? (0.42 * (double)sr) : 18000.0;
    /* Exact 4th-order Butterworth Qs: 1/(2*cos(3*pi/8)) and 1/(2*cos(pi/8)) */
    nova_biquad_set_lowpass(&ctx->aa[0], fc_aa, 0.54119610014619698, sr4);
    nova_biquad_set_lowpass(&ctx->aa[1], fc_aa, 1.3065629648763766, sr4);
    nova_biquad_reset(&ctx->aa[0]);
    nova_biquad_reset(&ctx->aa[1]);

    /* 4. DC Blocker @ 1x */
    ctx->dc[0].b0 = 1.0;
    ctx->dc[0].b1 = -1.0;
    ctx->dc[0].b2 = 0.0;
    ctx->dc[0].a1 = -0.996;
    ctx->dc[0].a2 = 0.0;
    nova_biquad_reset(&ctx->dc[0]);

    /* RMS envelope alpha (~80 ms) */
    ctx->env_alpha = 1.0 - exp(-1.0 / (0.080 * (double)sr));

    /* 5. Cabinet simulator (6 sections) */
    nova_biquad_set_highpass(&ctx->cab[0], 85.0, 0.707, sr);
    nova_biquad_set_peaking(&ctx->cab[1], 115.0, 3.0, 1.6, sr);
    nova_biquad_set_peaking(&ctx->cab[2], 380.0, -3.5, 1.1, sr);
    nova_biquad_set_peaking(&ctx->cab[3], 2350.0, 2.5, 1.4, sr);
    nova_biquad_set_lowpass(&ctx->cab[4], 4800.0, 0.541, sr);
    nova_biquad_set_lowpass(&ctx->cab[5], 4800.0, 1.307, sr);
    for (i = 0; i < 6; ++i) {
        nova_biquad_reset(&ctx->cab[i]);
    }

    ctx->tight_valid = 0;
    ctx->tone_valid = 0;
}

NOVA_API NovaGuitarAmp* NOVA_CALL nova_amp_create(int32_t sample_rate) {
    NovaGuitarAmp* ctx = (NovaGuitarAmp*)calloc(1, sizeof(NovaGuitarAmp));
    int32_t sr;
    if (!ctx) {
        return NULL;
    }
    sr = (sample_rate < 8000) ? 8000 : sample_rate;
    ctx->sr = 0;
    nova_amp_ensure_static_filters(ctx, sr);
    return ctx;
}

NOVA_API void NOVA_CALL nova_amp_destroy(NovaGuitarAmp* ctx) {
    if (ctx) {
        free(ctx);
    }
}

NOVA_API void NOVA_CALL nova_amp_reset(NovaGuitarAmp* ctx) {
    int i;
    if (!ctx) {
        return;
    }
    ctx->prev_sample[0] = 0.0;
    ctx->prev_sample[1] = 0.0;
    nova_biquad_reset(&ctx->pre[0]);
    nova_amp_reset_dist_states(ctx);
    for (i = 0; i < 3; ++i) {
        nova_biquad_reset(&ctx->tone[i]);
    }
    for (i = 0; i < 6; ++i) {
        nova_biquad_reset(&ctx->cab[i]);
    }
}

NOVA_API void NOVA_CALL nova_amp_process(
    NovaGuitarAmp* ctx,
    const float* in_interleaved,
    float* out_interleaved,
    int32_t frames,
    int32_t sample_rate,
    double distortion,
    double low_db,
    double mid_db,
    double treble_db,
    double volume,
    int32_t cab_enabled,
    float* out_in_peak_db,
    float* out_out_peak_db
) {
    int32_t sr, i, k;
    double dist, vol, peak, max_in;
    double total_db = 0.0, g1 = 1.0, g2 = 1.0, g3 = 1.0, g_total = 1.0, diode_mix = 0.0;
    double env_alpha, env_decay, env_in2, env_out2;
    double floor2_over_g2 = 1e-8;
    int dist_on;

    /* Local hoisted filter states & coeffs for register allocation */
    double pre_b0, pre_b1, pre_b2, pre_a1, pre_a2;
    double pre_zl0, pre_zl1, pre_zr0, pre_zr1;
    double prev_l, prev_r;

    double t0_b0, t0_b1, t0_b2, t0_a1, t0_a2, t0_zl0, t0_zl1, t0_zr0, t0_zr1;
    double t1_b0, t1_b1, t1_b2, t1_a1, t1_a2, t1_zl0, t1_zl1, t1_zr0, t1_zr1;
    double t2_b0, t2_b1, t2_b2, t2_a1, t2_a2, t2_zl0, t2_zl1, t2_zr0, t2_zr1;

    if (!ctx || !in_interleaved || !out_interleaved || frames <= 0) {
        return;
    }

    sr = (sample_rate < 8000) ? 8000 : sample_rate;
    nova_amp_ensure_static_filters(ctx, sr);

    /* Update tone stack if dirty (preserves filter state) */
    if (!ctx->tone_valid ||
        low_db != ctx->last_low_db ||
        mid_db != ctx->last_mid_db ||
        treble_db != ctx->last_treble_db) {
        nova_biquad_set_lowshelf(&ctx->tone[0], 110.0, low_db, 0.707, sr);
        nova_biquad_set_peaking(&ctx->tone[1], 720.0, mid_db, 0.85, sr);
        nova_biquad_set_highshelf(&ctx->tone[2], 3200.0, treble_db, 0.707, sr);
        ctx->last_low_db = low_db;
        ctx->last_mid_db = mid_db;
        ctx->last_treble_db = treble_db;
        ctx->tone_valid = 1;
    }

    dist = nova_clamp_d(distortion, 0.0, 1.0);
    dist_on = (dist > 0.002) ? 1 : 0;

    if (dist_on) {
        ctx->dist_active = 1;
        if (!ctx->tight_valid || dist != ctx->last_dist) {
            double fc_tight = 70.0 + 100.0 * pow(dist, 1.3);
            double bite_db = 6.0 * pow(dist, 1.2);
            nova_onepole_set_highpass(&ctx->tight[0], fc_tight, sr);
            nova_biquad_set_peaking(&ctx->tight[1], 1600.0, bite_db, 0.85, sr);
            ctx->last_dist = dist;
            ctx->tight_valid = 1;
        }
        total_db = 64.0 * pow(dist, 0.85);
        g1 = pow(10.0, (0.40 * total_db) / 20.0);
        g2 = pow(10.0, (0.36 * total_db) / 20.0);
        g3 = pow(10.0, (0.24 * total_db) / 20.0);
        g_total = g1 * g2 * g3;
        floor2_over_g2 = 1e-8 / (g_total * g_total);
        diode_mix = (dist > 0.35) ? (((dist - 0.35) / 0.65) * 0.55) : 0.0;
    } else if (ctx->dist_active) {
        nova_amp_reset_dist_states(ctx);
    }

    vol = (volume > 0.0) ? volume : 0.0;
    env_alpha = ctx->env_alpha;
    env_decay = 1.0 - env_alpha;
    env_in2 = ctx->env_in2;
    env_out2 = ctx->env_out2;
    peak = 0.0;
    max_in = 0.0;

    pre_b0 = ctx->pre[0].b0; pre_b1 = ctx->pre[0].b1; pre_b2 = ctx->pre[0].b2;
    pre_a1 = ctx->pre[0].a1; pre_a2 = ctx->pre[0].a2;
    pre_zl0 = ctx->pre[0].z_l[0]; pre_zl1 = ctx->pre[0].z_l[1];
    pre_zr0 = ctx->pre[0].z_r[0]; pre_zr1 = ctx->pre[0].z_r[1];
    prev_l = ctx->prev_sample[0];
    prev_r = ctx->prev_sample[1];

    t0_b0 = ctx->tone[0].b0; t0_b1 = ctx->tone[0].b1; t0_b2 = ctx->tone[0].b2; t0_a1 = ctx->tone[0].a1; t0_a2 = ctx->tone[0].a2;
    t0_zl0 = ctx->tone[0].z_l[0]; t0_zl1 = ctx->tone[0].z_l[1]; t0_zr0 = ctx->tone[0].z_r[0]; t0_zr1 = ctx->tone[0].z_r[1];
    t1_b0 = ctx->tone[1].b0; t1_b1 = ctx->tone[1].b1; t1_b2 = ctx->tone[1].b2; t1_a1 = ctx->tone[1].a1; t1_a2 = ctx->tone[1].a2;
    t1_zl0 = ctx->tone[1].z_l[0]; t1_zl1 = ctx->tone[1].z_l[1]; t1_zr0 = ctx->tone[1].z_r[0]; t1_zr1 = ctx->tone[1].z_r[1];
    t2_b0 = ctx->tone[2].b0; t2_b1 = ctx->tone[2].b1; t2_b2 = ctx->tone[2].b2; t2_a1 = ctx->tone[2].a1; t2_a2 = ctx->tone[2].a2;
    t2_zl0 = ctx->tone[2].z_l[0]; t2_zl1 = ctx->tone[2].z_l[1]; t2_zr0 = ctx->tone[2].z_r[0]; t2_zr1 = ctx->tone[2].z_r[1];

    if (dist_on) {
        double tg0_b0 = ctx->tight[0].b0, tg0_b1 = ctx->tight[0].b1, tg0_a1 = ctx->tight[0].a1;
        double tg0_zl0 = ctx->tight[0].z_l[0], tg0_zr0 = ctx->tight[0].z_r[0];
        double tg1_b0 = ctx->tight[1].b0, tg1_b1 = ctx->tight[1].b1, tg1_b2 = ctx->tight[1].b2, tg1_a1 = ctx->tight[1].a1, tg1_a2 = ctx->tight[1].a2;
        double tg1_zl0 = ctx->tight[1].z_l[0], tg1_zl1 = ctx->tight[1].z_l[1], tg1_zr0 = ctx->tight[1].z_r[0], tg1_zr1 = ctx->tight[1].z_r[1];

        double sag_b0 = ctx->sag1[0].b0, sag_p = -ctx->sag1[0].a1;
        double sag1_l = ctx->sag1[0].z_l[0], sag1_r = ctx->sag1[0].z_r[0];
        double sag2_l = ctx->sag2[0].z_l[0], sag2_r = ctx->sag2[0].z_r[0];
        double sag3_l = ctx->sag3[0].z_l[0], sag3_r = ctx->sag3[0].z_r[0];

        double ihp_b0 = ctx->inter1[0].b0, ihp_b1 = ctx->inter1[0].b1, ihp_a1 = ctx->inter1[0].a1;
        double ilp_b0 = ctx->inter1[1].b0, ilp_a1 = ctx->inter1[1].a1;
        double i1_hp_zl0 = ctx->inter1[0].z_l[0], i1_hp_zr0 = ctx->inter1[0].z_r[0];
        double i1_lp_zl0 = ctx->inter1[1].z_l[0], i1_lp_zr0 = ctx->inter1[1].z_r[0];
        double i2_hp_zl0 = ctx->inter2[0].z_l[0], i2_hp_zr0 = ctx->inter2[0].z_r[0];
        double i2_lp_zl0 = ctx->inter2[1].z_l[0], i2_lp_zr0 = ctx->inter2[1].z_r[0];

        double aa0_b0 = ctx->aa[0].b0, aa0_b1 = ctx->aa[0].b1, aa0_b2 = ctx->aa[0].b2, aa0_a1 = ctx->aa[0].a1, aa0_a2 = ctx->aa[0].a2;
        double aa0_zl0 = ctx->aa[0].z_l[0], aa0_zl1 = ctx->aa[0].z_l[1], aa0_zr0 = ctx->aa[0].z_r[0], aa0_zr1 = ctx->aa[0].z_r[1];
        double aa1_b0 = ctx->aa[1].b0, aa1_b1 = ctx->aa[1].b1, aa1_b2 = ctx->aa[1].b2, aa1_a1 = ctx->aa[1].a1, aa1_a2 = ctx->aa[1].a2;
        double aa1_zl0 = ctx->aa[1].z_l[0], aa1_zl1 = ctx->aa[1].z_l[1], aa1_zr0 = ctx->aa[1].z_r[0], aa1_zr1 = ctx->aa[1].z_r[1];

        double dc_zl0 = ctx->dc[0].z_l[0], dc_zr0 = ctx->dc[0].z_r[0];
        double d_bias1 = 0.05 * dist, d_bias2 = -0.07 * dist, d_bias3 = 0.08 * dist;
        double d_sag1 = 0.35 * dist * g1, d_sag2 = 0.32 * dist * g2, d_sag3 = 0.28 * dist * g3;
        NovaBiquad cab_loc[6];
        if (cab_enabled) {
            memcpy(cab_loc, ctx->cab, sizeof(cab_loc));
        }

        for (i = 0; i < frames; ++i) {
            double raw_l = nova_sanitize_d((double)in_interleaved[2 * i]);
            double raw_r = nova_sanitize_d((double)in_interleaved[2 * i + 1]);
            double x_l, x_r, xd_l, xd_r, diff_l, diff_r;
            double dec_l = 0.0, dec_r = 0.0;
            double p_in, p_out, g_comp;

            if (out_in_peak_db) {
                double al = fabs(raw_l), ar = fabs(raw_r);
                if (al > max_in) max_in = al;
                if (ar > max_in) max_in = ar;
            }

            /* 1. Pre-HPF */
            x_l = pre_b0 * raw_l + pre_zl0;
            pre_zl0 = pre_b1 * raw_l - pre_a1 * x_l + pre_zl1;
            pre_zl1 = pre_b2 * raw_l - pre_a2 * x_l;
            x_r = pre_b0 * raw_r + pre_zr0;
            pre_zr0 = pre_b1 * raw_r - pre_a1 * x_r + pre_zr1;
            pre_zr1 = pre_b2 * raw_r - pre_a2 * x_r;

            /* Tight HPF (1-pole) + Peaking */
            xd_l = tg0_b0 * x_l + tg0_zl0;
            tg0_zl0 = tg0_b1 * x_l - tg0_a1 * xd_l;
            xd_r = tg0_b0 * x_r + tg0_zr0;
            tg0_zr0 = tg0_b1 * x_r - tg0_a1 * xd_r;

            {
                double yt_l = tg1_b0 * xd_l + tg1_zl0;
                tg1_zl0 = tg1_b1 * xd_l - tg1_a1 * yt_l + tg1_zl1;
                tg1_zl1 = tg1_b2 * xd_l - tg1_a2 * yt_l;
                xd_l = yt_l;

                double yt_r = tg1_b0 * xd_r + tg1_zr0;
                tg1_zr0 = tg1_b1 * xd_r - tg1_a1 * yt_r + tg1_zr1;
                tg1_zr1 = tg1_b2 * xd_r - tg1_a2 * yt_r;
                xd_r = yt_r;
            }

            diff_l = xd_l - prev_l;
            diff_r = xd_r - prev_r;

            for (k = 1; k <= 4; ++k) {
                double frac = 0.25 * (double)k;
                double s_l = prev_l + frac * diff_l;
                double s_r = prev_r + frac * diff_r;
                double y1_l, y1_r, y2_l, y2_r, u3_l, u3_r, y3_l, y3_r, aa_l, aa_r;

                /* Stage 1 */
                sag1_l = sag_b0 * ((s_l > 0.0) ? s_l : 0.0) + sag_p * sag1_l;
                sag1_r = sag_b0 * ((s_r > 0.0) ? s_r : 0.0) + sag_p * sag1_r;
                {
                    double u1_l = s_l * g1 - d_sag1 * sag1_l + d_bias1;
                    double u1_r = s_r * g1 - d_sag1 * sag1_r + d_bias1;
                    double q1_l = u1_l * ((u1_l >= 0.0) ? (1.0 / 0.95) : (1.0 / 0.58));
                    double q1_r = u1_r * ((u1_r >= 0.0) ? (1.0 / 0.95) : (1.0 / 0.58));
                    y1_l = u1_l / sqrt(1.0 + q1_l * q1_l);
                    y1_r = u1_r / sqrt(1.0 + q1_r * q1_r);
                }
                {
                    double h_l = ihp_b0 * y1_l + i1_hp_zl0;
                    i1_hp_zl0 = ihp_b1 * y1_l - ihp_a1 * h_l;
                    y1_l = ilp_b0 * h_l + i1_lp_zl0;
                    i1_lp_zl0 = -ilp_a1 * y1_l;

                    double h_r = ihp_b0 * y1_r + i1_hp_zr0;
                    i1_hp_zr0 = ihp_b1 * y1_r - ihp_a1 * h_r;
                    y1_r = ilp_b0 * h_r + i1_lp_zr0;
                    i1_lp_zr0 = -ilp_a1 * y1_r;
                }

                /* Stage 2 */
                sag2_l = sag_b0 * ((-y1_l > 0.0) ? -y1_l : 0.0) + sag_p * sag2_l;
                sag2_r = sag_b0 * ((-y1_r > 0.0) ? -y1_r : 0.0) + sag_p * sag2_r;
                {
                    double u2_l = -y1_l * g2 - d_sag2 * sag2_l + d_bias2;
                    double u2_r = -y1_r * g2 - d_sag2 * sag2_r + d_bias2;
                    double q2_l = u2_l * ((u2_l >= 0.0) ? (1.0 / 0.88) : (1.0 / 0.54));
                    double q2_r = u2_r * ((u2_r >= 0.0) ? (1.0 / 0.88) : (1.0 / 0.54));
                    y2_l = u2_l / sqrt(1.0 + q2_l * q2_l);
                    y2_r = u2_r / sqrt(1.0 + q2_r * q2_r);
                }
                {
                    double h_l = ihp_b0 * y2_l + i2_hp_zl0;
                    i2_hp_zl0 = ihp_b1 * y2_l - ihp_a1 * h_l;
                    y2_l = ilp_b0 * h_l + i2_lp_zl0;
                    i2_lp_zl0 = -ilp_a1 * y2_l;

                    double h_r = ihp_b0 * y2_r + i2_hp_zr0;
                    i2_hp_zr0 = ihp_b1 * y2_r - ihp_a1 * h_r;
                    y2_r = ilp_b0 * h_r + i2_lp_zr0;
                    i2_lp_zr0 = -ilp_a1 * y2_r;
                }

                /* Stage 3 */
                sag3_l = sag_b0 * ((y2_l > 0.0) ? y2_l : 0.0) + sag_p * sag3_l;
                sag3_r = sag_b0 * ((y2_r > 0.0) ? y2_r : 0.0) + sag_p * sag3_r;
                u3_l = -y2_l * g3 + d_sag3 * sag3_l + d_bias3;
                u3_r = -y2_r * g3 + d_sag3 * sag3_r + d_bias3;
                y3_l = nova_fast_tanh(u3_l);
                y3_r = nova_fast_tanh(u3_r);
                if (diode_mix > 0.0) {
                    double hard_l = nova_clamp_d(u3_l * 0.92, -0.76, 1.04);
                    double hard_r = nova_clamp_d(u3_r * 0.92, -0.76, 1.04);
                    y3_l = (1.0 - diode_mix) * y3_l + diode_mix * hard_l;
                    y3_r = (1.0 - diode_mix) * y3_r + diode_mix * hard_r;
                }

                /* Anti-aliasing 4x */
                {
                    double t_l = aa0_b0 * y3_l + aa0_zl0;
                    aa0_zl0 = aa0_b1 * y3_l - aa0_a1 * t_l + aa0_zl1;
                    aa0_zl1 = aa0_b2 * y3_l - aa0_a2 * t_l;
                    aa_l = aa1_b0 * t_l + aa1_zl0;
                    aa1_zl0 = aa1_b1 * t_l - aa1_a1 * aa_l + aa1_zl1;
                    aa1_zl1 = aa1_b2 * t_l - aa1_a2 * aa_l;

                    double t_r = aa0_b0 * y3_r + aa0_zr0;
                    aa0_zr0 = aa0_b1 * y3_r - aa0_a1 * t_r + aa0_zr1;
                    aa0_zr1 = aa0_b2 * y3_r - aa0_a2 * t_r;
                    aa_r = aa1_b0 * t_r + aa1_zr0;
                    aa1_zr0 = aa1_b1 * t_r - aa1_a1 * aa_r + aa1_zr1;
                    aa1_zr1 = aa1_b2 * t_r - aa1_a2 * aa_r;
                }

                if (k == 4) {
                    dec_l = aa_l;
                    dec_r = aa_r;
                }
            }
            prev_l = xd_l;
            prev_r = xd_r;

            /* DC Blocker (b0=1, b1=-1, a1=-0.996) */
            {
                double dc_l = dec_l + dc_zl0;
                dc_zl0 = -dec_l + 0.996 * dc_l;
                dec_l = dc_l;

                double dc_r = dec_r + dc_zr0;
                dc_zr0 = -dec_r + 0.996 * dc_r;
                dec_r = dc_r;
            }

            /* Coupled Stereo RMS Auto-Gain */
            p_in = 0.5 * (x_l * x_l + x_r * x_r);
            p_out = 0.5 * (dec_l * dec_l + dec_r * dec_r);
            env_in2 = env_alpha * p_in + env_decay * env_in2;
            env_out2 = env_alpha * p_out + env_decay * env_out2;
            g_comp = sqrt((env_in2 + floor2_over_g2) / (env_out2 + 1e-8));
            x_l = dec_l * g_comp;
            x_r = dec_r * g_comp;

            /* 3. Tone Stack (3 sections) */
            {
                double y_l = t0_b0 * x_l + t0_zl0;
                t0_zl0 = t0_b1 * x_l - t0_a1 * y_l + t0_zl1;
                t0_zl1 = t0_b2 * x_l - t0_a2 * y_l;
                x_l = t1_b0 * y_l + t1_zl0;
                t1_zl0 = t1_b1 * y_l - t1_a1 * x_l + t1_zl1;
                t1_zl1 = t1_b2 * y_l - t1_a2 * x_l;
                y_l = t2_b0 * x_l + t2_zl0;
                t2_zl0 = t2_b1 * x_l - t2_a1 * y_l + t2_zl1;
                t2_zl1 = t2_b2 * x_l - t2_a2 * y_l;
                x_l = y_l;

                double y_r = t0_b0 * x_r + t0_zr0;
                t0_zr0 = t0_b1 * x_r - t0_a1 * y_r + t0_zr1;
                t0_zr1 = t0_b2 * x_r - t0_a2 * y_r;
                x_r = t1_b0 * y_r + t1_zr0;
                t1_zr0 = t1_b1 * y_r - t1_a1 * x_r + t1_zr1;
                t1_zr1 = t1_b2 * y_r - t1_a2 * x_r;
                y_r = t2_b0 * x_r + t2_zr0;
                t2_zr0 = t2_b1 * x_r - t2_a1 * y_r + t2_zr1;
                t2_zr1 = t2_b2 * x_r - t2_a2 * y_r;
                x_r = y_r;
            }

            /* 4. Cabinet Sim (6 sections) */
            if (cab_enabled) {
                x_l = nova_biquad_step_l(&cab_loc[0], x_l);
                x_l = nova_biquad_step_l(&cab_loc[1], x_l);
                x_l = nova_biquad_step_l(&cab_loc[2], x_l);
                x_l = nova_biquad_step_l(&cab_loc[3], x_l);
                x_l = nova_biquad_step_l(&cab_loc[4], x_l);
                x_l = nova_biquad_step_l(&cab_loc[5], x_l);
                x_r = nova_biquad_step_r(&cab_loc[0], x_r);
                x_r = nova_biquad_step_r(&cab_loc[1], x_r);
                x_r = nova_biquad_step_r(&cab_loc[2], x_r);
                x_r = nova_biquad_step_r(&cab_loc[3], x_r);
                x_r = nova_biquad_step_r(&cab_loc[4], x_r);
                x_r = nova_biquad_step_r(&cab_loc[5], x_r);
            }

            /* 5. Master Volume & Peak Scan */
            x_l *= vol;
            x_r *= vol;
            {
                double al = fabs(x_l), ar = fabs(x_r);
                if (al > peak) peak = al;
                if (ar > peak) peak = ar;
            }
            out_interleaved[2 * i] = nova_sanitize_f32_out(x_l);
            out_interleaved[2 * i + 1] = nova_sanitize_f32_out(x_r);
        }

        ctx->tight[0].z_l[0] = nova_flush_denormal_d(tg0_zl0);
        ctx->tight[0].z_r[0] = nova_flush_denormal_d(tg0_zr0);
        ctx->tight[1].z_l[0] = nova_flush_denormal_d(tg1_zl0);
        ctx->tight[1].z_l[1] = nova_flush_denormal_d(tg1_zl1);
        ctx->tight[1].z_r[0] = nova_flush_denormal_d(tg1_zr0);
        ctx->tight[1].z_r[1] = nova_flush_denormal_d(tg1_zr1);

        ctx->sag1[0].z_l[0] = nova_flush_denormal_d(sag1_l);
        ctx->sag1[0].z_r[0] = nova_flush_denormal_d(sag1_r);
        ctx->sag2[0].z_l[0] = nova_flush_denormal_d(sag2_l);
        ctx->sag2[0].z_r[0] = nova_flush_denormal_d(sag2_r);
        ctx->sag3[0].z_l[0] = nova_flush_denormal_d(sag3_l);
        ctx->sag3[0].z_r[0] = nova_flush_denormal_d(sag3_r);

        ctx->inter1[0].z_l[0] = nova_flush_denormal_d(i1_hp_zl0);
        ctx->inter1[0].z_r[0] = nova_flush_denormal_d(i1_hp_zr0);
        ctx->inter1[1].z_l[0] = nova_flush_denormal_d(i1_lp_zl0);
        ctx->inter1[1].z_r[0] = nova_flush_denormal_d(i1_lp_zr0);
        ctx->inter2[0].z_l[0] = nova_flush_denormal_d(i2_hp_zl0);
        ctx->inter2[0].z_r[0] = nova_flush_denormal_d(i2_hp_zr0);
        ctx->inter2[1].z_l[0] = nova_flush_denormal_d(i2_lp_zl0);
        ctx->inter2[1].z_r[0] = nova_flush_denormal_d(i2_lp_zr0);

        ctx->aa[0].z_l[0] = nova_flush_denormal_d(aa0_zl0);
        ctx->aa[0].z_l[1] = nova_flush_denormal_d(aa0_zl1);
        ctx->aa[0].z_r[0] = nova_flush_denormal_d(aa0_zr0);
        ctx->aa[0].z_r[1] = nova_flush_denormal_d(aa0_zr1);
        ctx->aa[1].z_l[0] = nova_flush_denormal_d(aa1_zl0);
        ctx->aa[1].z_l[1] = nova_flush_denormal_d(aa1_zl1);
        ctx->aa[1].z_r[0] = nova_flush_denormal_d(aa1_zr0);
        ctx->aa[1].z_r[1] = nova_flush_denormal_d(aa1_zr1);

        ctx->dc[0].z_l[0] = nova_flush_denormal_d(dc_zl0);
        ctx->dc[0].z_r[0] = nova_flush_denormal_d(dc_zr0);
        if (cab_enabled) {
            memcpy(ctx->cab, cab_loc, sizeof(cab_loc));
        }
    } else {
        for (i = 0; i < frames; ++i) {
            double raw_l = nova_sanitize_d((double)in_interleaved[2 * i]);
            double raw_r = nova_sanitize_d((double)in_interleaved[2 * i + 1]);
            double x_l, x_r;

            if (out_in_peak_db) {
                double al = fabs(raw_l), ar = fabs(raw_r);
                if (al > max_in) max_in = al;
                if (ar > max_in) max_in = ar;
            }

            /* 1. Pre-HPF */
            x_l = pre_b0 * raw_l + pre_zl0;
            pre_zl0 = pre_b1 * raw_l - pre_a1 * x_l + pre_zl1;
            pre_zl1 = pre_b2 * raw_l - pre_a2 * x_l;
            x_r = pre_b0 * raw_r + pre_zr0;
            pre_zr0 = pre_b1 * raw_r - pre_a1 * x_r + pre_zr1;
            pre_zr1 = pre_b2 * raw_r - pre_a2 * x_r;

            prev_l = x_l;
            prev_r = x_r;

            /* 3. Tone Stack */
            {
                double y_l = t0_b0 * x_l + t0_zl0;
                t0_zl0 = t0_b1 * x_l - t0_a1 * y_l + t0_zl1;
                t0_zl1 = t0_b2 * x_l - t0_a2 * y_l;
                x_l = t1_b0 * y_l + t1_zl0;
                t1_zl0 = t1_b1 * y_l - t1_a1 * x_l + t1_zl1;
                t1_zl1 = t1_b2 * y_l - t1_a2 * x_l;
                y_l = t2_b0 * x_l + t2_zl0;
                t2_zl0 = t2_b1 * x_l - t2_a1 * y_l + t2_zl1;
                t2_zl1 = t2_b2 * x_l - t2_a2 * y_l;
                x_l = y_l;

                double y_r = t0_b0 * x_r + t0_zr0;
                t0_zr0 = t0_b1 * x_r - t0_a1 * y_r + t0_zr1;
                t0_zr1 = t0_b2 * x_r - t0_a2 * y_r;
                x_r = t1_b0 * y_r + t1_zr0;
                t1_zr0 = t1_b1 * y_r - t1_a1 * x_r + t1_zr1;
                t1_zr1 = t1_b2 * y_r - t1_a2 * x_r;
                y_r = t2_b0 * x_r + t2_zr0;
                t2_zr0 = t2_b1 * x_r - t2_a1 * y_r + t2_zr1;
                t2_zr1 = t2_b2 * x_r - t2_a2 * y_r;
                x_r = y_r;
            }

            /* 4. Cabinet Sim */
            if (cab_enabled) {
                int c;
                for (c = 0; c < 6; ++c) {
                    x_l = nova_biquad_step_l(&ctx->cab[c], x_l);
                    x_r = nova_biquad_step_r(&ctx->cab[c], x_r);
                }
            }

            /* 5. Master Volume & Peak Scan */
            x_l *= vol;
            x_r *= vol;
            {
                double al = fabs(x_l), ar = fabs(x_r);
                if (al > peak) peak = al;
                if (ar > peak) peak = ar;
            }
            out_interleaved[2 * i] = nova_sanitize_f32_out(x_l);
            out_interleaved[2 * i + 1] = nova_sanitize_f32_out(x_r);
        }
    }

    ctx->env_in2 = nova_flush_denormal_d(env_in2);
    ctx->env_out2 = nova_flush_denormal_d(env_out2);
    ctx->prev_sample[0] = nova_flush_denormal_d(prev_l);
    ctx->prev_sample[1] = nova_flush_denormal_d(prev_r);

    ctx->pre[0].z_l[0] = nova_flush_denormal_d(pre_zl0);
    ctx->pre[0].z_l[1] = nova_flush_denormal_d(pre_zl1);
    ctx->pre[0].z_r[0] = nova_flush_denormal_d(pre_zr0);
    ctx->pre[0].z_r[1] = nova_flush_denormal_d(pre_zr1);

    ctx->tone[0].z_l[0] = nova_flush_denormal_d(t0_zl0);
    ctx->tone[0].z_l[1] = nova_flush_denormal_d(t0_zl1);
    ctx->tone[0].z_r[0] = nova_flush_denormal_d(t0_zr0);
    ctx->tone[0].z_r[1] = nova_flush_denormal_d(t0_zr1);
    ctx->tone[1].z_l[0] = nova_flush_denormal_d(t1_zl0);
    ctx->tone[1].z_l[1] = nova_flush_denormal_d(t1_zl1);
    ctx->tone[1].z_r[0] = nova_flush_denormal_d(t1_zr0);
    ctx->tone[1].z_r[1] = nova_flush_denormal_d(t1_zr1);
    ctx->tone[2].z_l[0] = nova_flush_denormal_d(t2_zl0);
    ctx->tone[2].z_l[1] = nova_flush_denormal_d(t2_zl1);
    ctx->tone[2].z_r[0] = nova_flush_denormal_d(t2_zr0);
    ctx->tone[2].z_r[1] = nova_flush_denormal_d(t2_zr1);

    for (i = 0; i < 6; ++i) nova_biquad_flush(&ctx->cab[i]);

    /* Soft-clip anti-clipping protection if peak > 0.98 */
    if (peak > 0.98) {
        double max_clipped = 0.0;
        int32_t total = frames * 2;
        int32_t j;
        for (j = 0; j < total; ++j) {
            double v = tanh((double)out_interleaved[j] * 0.90) * 1.05;
            double av = fabs(v);
            if (av > max_clipped) max_clipped = av;
            out_interleaved[j] = nova_sanitize_f32_out(v);
        }
        peak = max_clipped;
    }

    if (out_in_peak_db) {
        double clamped_in = (max_in > 1e-5) ? max_in : 1e-5;
        *out_in_peak_db = (float)(20.0 * log10(clamped_in));
    }
    if (out_out_peak_db) {
        double clamped_out = (peak > 1e-5) ? peak : 1e-5;
        *out_out_peak_db = (float)(20.0 * log10(clamped_out));
    }
}

/* --------------------------------------------------------------------------
 * 2. Equalizer Kernel (Cascaded Stereo Biquads with Closed-Form lfilter_zi)
 * -------------------------------------------------------------------------- */

typedef struct NovaEqBand {
    double b0, b1, b2, a1, a2;
    double z_l[2];
    double z_r[2];
    int32_t active;
    int32_t zi_initialized;
} NovaEqBand;

typedef struct NovaEqualizer {
    int32_t num_bands;
    NovaEqBand bands[NOVA_EQ_MAX_BANDS];
} NovaEqualizer;

NOVA_API NovaEqualizer* NOVA_CALL nova_eq_create(int32_t num_bands) {
    NovaEqualizer* ctx = (NovaEqualizer*)calloc(1, sizeof(NovaEqualizer));
    if (!ctx) {
        return NULL;
    }
    if (num_bands < 1) num_bands = 1;
    if (num_bands > NOVA_EQ_MAX_BANDS) num_bands = NOVA_EQ_MAX_BANDS;
    ctx->num_bands = num_bands;
    return ctx;
}

NOVA_API void NOVA_CALL nova_eq_destroy(NovaEqualizer* ctx) {
    if (ctx) {
        free(ctx);
    }
}

NOVA_API void NOVA_CALL nova_eq_reset_band(NovaEqualizer* ctx, int32_t band_idx) {
    if (!ctx || band_idx < 0 || band_idx >= NOVA_EQ_MAX_BANDS) {
        return;
    }
    ctx->bands[band_idx].z_l[0] = 0.0;
    ctx->bands[band_idx].z_l[1] = 0.0;
    ctx->bands[band_idx].z_r[0] = 0.0;
    ctx->bands[band_idx].z_r[1] = 0.0;
    ctx->bands[band_idx].zi_initialized = 0;
}

NOVA_API void NOVA_CALL nova_eq_reset(NovaEqualizer* ctx) {
    int32_t i;
    if (!ctx) {
        return;
    }
    for (i = 0; i < NOVA_EQ_MAX_BANDS; ++i) {
        nova_eq_reset_band(ctx, i);
    }
}

NOVA_API void NOVA_CALL nova_eq_set_num_bands(NovaEqualizer* ctx, int32_t num_bands) {
    if (!ctx) {
        return;
    }
    if (num_bands < 1) num_bands = 1;
    if (num_bands > NOVA_EQ_MAX_BANDS) num_bands = NOVA_EQ_MAX_BANDS;
    ctx->num_bands = num_bands;
}

NOVA_API void NOVA_CALL nova_eq_set_band(
    NovaEqualizer* ctx,
    int32_t band_idx,
    double b0,
    double b1,
    double b2,
    double a1,
    double a2,
    int32_t active,
    int32_t reset_zi
) {
    NovaEqBand* b;
    if (!ctx || band_idx < 0 || band_idx >= NOVA_EQ_MAX_BANDS) {
        return;
    }
    b = &ctx->bands[band_idx];
    b->b0 = b0;
    b->b1 = b1;
    b->b2 = b2;
    b->a1 = a1;
    b->a2 = a2;
    b->active = active ? 1 : 0;
    if (reset_zi) {
        b->z_l[0] = 0.0;
        b->z_l[1] = 0.0;
        b->z_r[0] = 0.0;
        b->z_r[1] = 0.0;
        b->zi_initialized = 0;
    }
}

NOVA_API void NOVA_CALL nova_eq_process(
    NovaEqualizer* ctx,
    const float* in_interleaved,
    float* out_interleaved,
    int32_t frames,
    double master_gain_lin
) {
    int32_t i, b_idx;
    int32_t total;
    if (!ctx || !in_interleaved || !out_interleaved || frames <= 0) {
        return;
    }

    total = frames * 2;
    for (i = 0; i < total; ++i) {
        double v = nova_sanitize_d((double)in_interleaved[i]);
        out_interleaved[i] = nova_sanitize_f32_out(v);
    }

    for (b_idx = 0; b_idx < ctx->num_bands; ++b_idx) {
        NovaEqBand* band = &ctx->bands[b_idx];
        double b0, b1, b2, a1, a2;
        double zl0, zl1, zr0, zr1;
        if (!band->active) {
            continue;
        }
        b0 = band->b0;
        b1 = band->b1;
        b2 = band->b2;
        a1 = band->a1;
        a2 = band->a2;

        if (!band->zi_initialized) {
            double denom = 1.0 + a1 + a2;
            double dc = (fabs(denom) > 1e-12) ? ((b0 + b1 + b2) / denom) : 0.0;
            double zi0 = dc - b0;
            double zi1 = b2 - a2 * dc;
            double x0_l = (double)out_interleaved[0];
            double x0_r = (double)out_interleaved[1];
            band->z_l[0] = zi0 * x0_l;
            band->z_l[1] = zi1 * x0_l;
            band->z_r[0] = zi0 * x0_r;
            band->z_r[1] = zi1 * x0_r;
            band->zi_initialized = 1;
        }

        zl0 = band->z_l[0];
        zl1 = band->z_l[1];
        zr0 = band->z_r[0];
        zr1 = band->z_r[1];

        for (i = 0; i < frames; ++i) {
            double xl = (double)out_interleaved[2 * i];
            double xr = (double)out_interleaved[2 * i + 1];
            double yl = b0 * xl + zl0;
            double yr = b0 * xr + zr0;

            zl0 = b1 * xl - a1 * yl + zl1;
            zl1 = b2 * xl - a2 * yl;
            zr0 = b1 * xr - a1 * yr + zr1;
            zr1 = b2 * xr - a2 * yr;

            out_interleaved[2 * i] = nova_sanitize_f32_out(yl);
            out_interleaved[2 * i + 1] = nova_sanitize_f32_out(yr);
        }

        band->z_l[0] = nova_flush_denormal_d(zl0);
        band->z_l[1] = nova_flush_denormal_d(zl1);
        band->z_r[0] = nova_flush_denormal_d(zr0);
        band->z_r[1] = nova_flush_denormal_d(zr1);
    }

    if (fabs(master_gain_lin - 1.0) > 1e-9) {
        for (i = 0; i < total; ++i) {
            double v = (double)out_interleaved[i] * master_gain_lin;
            out_interleaved[i] = nova_sanitize_f32_out(v);
        }
    }
}

/* --------------------------------------------------------------------------
 * 3. Compressor Kernel (Caller-Owned POD State, Giannoulis Decoupled Env)
 * -------------------------------------------------------------------------- */

typedef struct NovaCompressorState {
    double env_peak;
    double env_gr_db;
    double hpf_z0;
    double hpf_z1;
    double cached_hpf_freq;
    int32_t cached_hpf_sr;
    int32_t hpf_initialized;
    double hpf_b0;
    double hpf_b1;
    double hpf_b2;
    double hpf_a1;
    double hpf_a2;
} NovaCompressorState;

NOVA_API void NOVA_CALL nova_comp_reset(NovaCompressorState* st) {
    if (!st) {
        return;
    }
    memset(st, 0, sizeof(NovaCompressorState));
}

NOVA_API void NOVA_CALL nova_comp_process(
    NovaCompressorState* st,
    const float* in_stereo,
    float* out_stereo,
    int32_t frames,
    int32_t sample_rate,
    float threshold_db,
    float ratio,
    float attack_ms,
    float release_ms,
    float knee_db,
    float makeup_gain_db,
    int32_t auto_makeup,
    float mix,
    int32_t detection_mode,
    float sidechain_hpf_hz,
    int32_t enabled,
    float* out_in_peak_db,
    float* out_out_peak_db,
    float* out_gr_db
) {
    int32_t sr_i, i;
    double sr, max_in = 0.0, max_out = 0.0, min_gr_db = 0.0;
    double att_sec, rel_sec, det_rel_sec, alpha_det_rel, alpha_att, alpha_rel, om_att, om_rel;
    double t_db, r, slope, knee, half_k, two_k, auto_db, makeup_lin, mix_val, dry_mix;
    double cur_pk, cur_gr, rms_alpha, om_rms;
    int hpf_active;

    if (!st || !in_stereo || !out_stereo || frames <= 0) {
        if (out_gr_db) *out_gr_db = 0.0f;
        return;
    }

    sr_i = (sample_rate > 0) ? sample_rate : 44100;
    sr = (double)sr_i;

    if (!enabled) {
        for (i = 0; i < frames; ++i) {
            float l = in_stereo[2 * i];
            float r_ch = in_stereo[2 * i + 1];
            double al = isfinite((double)l) ? fabs((double)l) : 0.0;
            double ar = isfinite((double)r_ch) ? fabs((double)r_ch) : 0.0;
            if (al > max_in) max_in = al;
            if (ar > max_in) max_in = ar;
            if (out_stereo != in_stereo) {
                out_stereo[2 * i] = l;
                out_stereo[2 * i + 1] = r_ch;
            }
        }
        {
            float pk_db = (float)(20.0 * log10((max_in > 1e-5) ? max_in : 1e-5));
            if (out_in_peak_db) *out_in_peak_db = pk_db;
            if (out_out_peak_db) *out_out_peak_db = pk_db;
            if (out_gr_db) *out_gr_db = 0.0f;
        }
        return;
    }

    hpf_active = (sidechain_hpf_hz >= 20.0f) ? 1 : 0;
    if (hpf_active) {
        double hpf_freq = (double)sidechain_hpf_hz;
        if (st->cached_hpf_freq != hpf_freq || st->cached_hpf_sr != sr_i) {
            double f = nova_clamp_d(hpf_freq, 10.0, sr * 0.45);
            double w0 = 2.0 * M_PI * f / sr;
            double cos_w0 = cos(w0);
            double sin_w0 = sin(w0);
            double alpha = sin_w0 / (2.0 * 0.70710678);
            double b0 = (1.0 + cos_w0) * 0.5;
            double b1 = -(1.0 + cos_w0);
            double b2 = (1.0 + cos_w0) * 0.5;
            double a0 = 1.0 + alpha;
            double a1 = -2.0 * cos_w0;
            double a2 = 1.0 - alpha;

            st->hpf_b0 = b0 / a0;
            st->hpf_b1 = b1 / a0;
            st->hpf_b2 = b2 / a0;
            st->hpf_a1 = a1 / a0;
            st->hpf_a2 = a2 / a0;
            st->cached_hpf_freq = hpf_freq;
            st->cached_hpf_sr = sr_i;
            st->hpf_z0 = 0.0;
            st->hpf_z1 = 0.0;
            st->hpf_initialized = 0;
        }
    }

    att_sec = ((double)attack_ms / 1000.0 > 0.0001) ? ((double)attack_ms / 1000.0) : 0.0001;
    rel_sec = ((double)release_ms / 1000.0 > 0.001) ? ((double)release_ms / 1000.0) : 0.001;
    det_rel_sec = (rel_sec > 0.015) ? rel_sec : 0.015;

    alpha_det_rel = exp(-1.0 / (det_rel_sec * sr));
    alpha_att = exp(-1.0 / (att_sec * sr));
    alpha_rel = exp(-1.0 / (rel_sec * sr));
    om_att = 1.0 - alpha_att;
    om_rel = 1.0 - alpha_rel;

    t_db = (double)threshold_db;
    r = ((double)ratio > 1.0) ? (double)ratio : 1.0;
    slope = (1.0 / r) - 1.0;
    knee = ((double)knee_db > 0.0) ? (double)knee_db : 0.0;
    half_k = knee * 0.5;
    two_k = (knee > 0.0) ? (2.0 * knee) : 1.0;

    auto_db = auto_makeup ? (-(t_db * (1.0 - 1.0 / r)) * 0.5) : 0.0;
    makeup_lin = pow(10.0, ((double)makeup_gain_db + auto_db) / 20.0);
    mix_val = nova_clamp_d((double)mix, 0.0, 1.0);
    dry_mix = 1.0 - mix_val;

    cur_pk = st->env_peak;
    cur_gr = st->env_gr_db;
    rms_alpha = exp(-1.0 / (0.015 * sr));
    om_rms = 1.0 - rms_alpha;

    for (i = 0; i < frames; ++i) {
        double l = nova_sanitize_d((double)in_stereo[2 * i]);
        double r_ch = nova_sanitize_d((double)in_stereo[2 * i + 1]);
        double al = fabs(l);
        double ar = fabs(r_ch);
        double sc = (al > ar) ? al : ar;
        double det, x_db, diff, tgt_gr, gain_lin, wet_gain, out_l, out_r;
        double aol, aor;

        if (sc > max_in) max_in = sc;

        if (hpf_active) {
            double y;
            if (!st->hpf_initialized) {
                double denom = 1.0 + st->hpf_a1 + st->hpf_a2;
                double y_ss = (fabs(denom) > 1e-12) ? ((st->hpf_b0 + st->hpf_b1 + st->hpf_b2) / denom) : 0.0;
                st->hpf_z0 = (y_ss - st->hpf_b0) * sc;
                st->hpf_z1 = (st->hpf_b2 - st->hpf_a2 * y_ss) * sc;
                st->hpf_initialized = 1;
            }
            y = st->hpf_b0 * sc + st->hpf_z0;
            st->hpf_z0 = st->hpf_b1 * sc - st->hpf_a1 * y + st->hpf_z1;
            st->hpf_z1 = st->hpf_b2 * sc - st->hpf_a2 * y;
            sc = fabs(y);
        }

        if (detection_mode == 1) {
            cur_pk = rms_alpha * cur_pk + om_rms * (sc * sc);
            det = sqrt((cur_pk > 1e-10) ? cur_pk : 1e-10);
        } else {
            if (sc > cur_pk) {
                cur_pk = sc;
            } else {
                cur_pk = alpha_det_rel * cur_pk;
            }
            det = cur_pk;
        }

        x_db = (det > 1e-5) ? (8.685889638065037 * log((det > 1e-10) ? det : 1e-10)) : -100.0;
        diff = x_db - t_db;
        if (knee > 0.1) {
            if (diff <= -half_k) {
                tgt_gr = 0.0;
            } else if (diff >= half_k) {
                tgt_gr = slope * diff;
            } else {
                double d_k = diff + half_k;
                tgt_gr = slope * (d_k * d_k) / two_k;
            }
        } else {
            tgt_gr = (diff > 0.0) ? (slope * diff) : 0.0;
        }

        if (tgt_gr < cur_gr) {
            cur_gr = alpha_att * cur_gr + om_att * tgt_gr;
        } else {
            cur_gr = alpha_rel * cur_gr + om_rel * tgt_gr;
        }
        if (i == 0 || cur_gr < min_gr_db) {
            min_gr_db = cur_gr;
        }

        gain_lin = pow(10.0, cur_gr / 20.0) * makeup_lin;
        wet_gain = dry_mix + mix_val * gain_lin;
        out_l = l * wet_gain;
        out_r = r_ch * wet_gain;

        aol = fabs(out_l);
        aor = fabs(out_r);
        if (aol > max_out) max_out = aol;
        if (aor > max_out) max_out = aor;

        out_stereo[2 * i] = nova_sanitize_f32_out(out_l);
        out_stereo[2 * i + 1] = nova_sanitize_f32_out(out_r);
    }

    st->env_peak = nova_flush_denormal_d(cur_pk);
    st->env_gr_db = nova_flush_denormal_d(cur_gr);
    st->hpf_z0 = nova_flush_denormal_d(st->hpf_z0);
    st->hpf_z1 = nova_flush_denormal_d(st->hpf_z1);

    if (out_in_peak_db) {
        *out_in_peak_db = (float)(20.0 * log10((max_in > 1e-5) ? max_in : 1e-5));
    }
    if (out_out_peak_db) {
        *out_out_peak_db = (float)(20.0 * log10((max_out > 1e-5) ? max_out : 1e-5));
    }
    if (out_gr_db) {
        *out_gr_db = (float)min_gr_db;
    }
}

/* --------------------------------------------------------------------------
 * 4. Mixer Kernel (Gain / Pan / Mono / Dim / Optional Soft-Limit / Meters)
 * -------------------------------------------------------------------------- */

NOVA_API void NOVA_CALL nova_mixer_process(
    const float* in_stereo,
    float* out_stereo,
    int32_t frames,
    int32_t sample_rate,
    float master_volume,
    float master_pan,
    int32_t mono_switch,
    int32_t dim_switch,
    int32_t soft_limit,
    float* io_peak_left_db,
    float* io_peak_right_db,
    float* out_rms_left_db,
    float* out_rms_right_db
) {
    int32_t sr_i, i;
    double vol, pan, gain_l, gain_r;
    double max_l = 0.0, max_r = 0.0;
    double sum_sq_l = 0.0, sum_sq_r = 0.0;
    double cur_l, cur_r, dt_sec, decay_db, rms_l, rms_r;

    if (!in_stereo || !out_stereo || frames <= 0) {
        return;
    }

    sr_i = (sample_rate <= 0) ? 44100 : ((sample_rate < 8000) ? 8000 : sample_rate);
    vol = (double)master_volume * (dim_switch ? 0.25 : 1.0);
    pan = nova_clamp_d((double)master_pan, -1.0, 1.0);
    gain_l = vol * (1.0 - ((pan > 0.0) ? pan : 0.0));
    gain_r = vol * (1.0 + ((pan < 0.0) ? pan : 0.0));

    for (i = 0; i < frames; ++i) {
        double l = nova_sanitize_d((double)in_stereo[2 * i]);
        double r = nova_sanitize_d((double)in_stereo[2 * i + 1]);
        double al, ar;

        if (mono_switch) {
            double m = 0.5 * (l + r);
            l = m;
            r = m;
        }

        l *= gain_l;
        r *= gain_r;

        if (soft_limit) {
            l = tanh(l);
            r = tanh(r);
        }

        al = fabs(l);
        ar = fabs(r);
        if (al > max_l) max_l = al;
        if (ar > max_r) max_r = ar;
        sum_sq_l += l * l;
        sum_sq_r += r * r;

        out_stereo[2 * i] = nova_sanitize_f32_out(l);
        out_stereo[2 * i + 1] = nova_sanitize_f32_out(r);
    }

    cur_l = 20.0 * log10((max_l > 1e-5) ? max_l : 1e-5);
    cur_r = 20.0 * log10((max_r > 1e-5) ? max_r : 1e-5);
    dt_sec = (double)frames / (double)sr_i;
    decay_db = (18.0 * dt_sec < 0.5) ? (18.0 * dt_sec) : 0.5;

    if (io_peak_left_db) {
        double prev = isfinite((double)*io_peak_left_db) ? (double)*io_peak_left_db : -60.0;
        double pk = prev - decay_db;
        if (cur_l > pk) pk = cur_l;
        if (pk < -60.0) pk = -60.0;
        *io_peak_left_db = (float)pk;
    }
    if (io_peak_right_db) {
        double prev = isfinite((double)*io_peak_right_db) ? (double)*io_peak_right_db : -60.0;
        double pk = prev - decay_db;
        if (cur_r > pk) pk = cur_r;
        if (pk < -60.0) pk = -60.0;
        *io_peak_right_db = (float)pk;
    }
    if (out_rms_left_db) {
        rms_l = sqrt(sum_sq_l / (double)frames);
        *out_rms_left_db = (float)(20.0 * log10((rms_l > 1e-5) ? rms_l : 1e-5));
    }
    if (out_rms_right_db) {
        rms_r = sqrt(sum_sq_r / (double)frames);
        *out_rms_right_db = (float)(20.0 * log10((rms_r > 1e-5) ? rms_r : 1e-5));
    }
}

/* --------------------------------------------------------------------------
 * Shared Interpolation Helpers (M1b)
 * -------------------------------------------------------------------------- */

static double nova_interp3(double x, double x0, double x1, double x2, double y0, double y1, double y2) {
    if (x <= x0) return y0;
    if (x >= x2) return y2;
    if (x <= x1) {
        double t = (x - x0) / (x1 - x0);
        return y0 + t * (y1 - y0);
    } else {
        double t = (x - x1) / (x2 - x1);
        return y1 + t * (y2 - y1);
    }
}

static double nova_interp5(double x, const double* xp, const double* fp) {
    int i;
    if (x <= xp[0]) return fp[0];
    if (x >= xp[4]) return fp[4];
    for (i = 0; i < 4; ++i) {
        if (x <= xp[i + 1]) {
            double t = (x - xp[i]) / (xp[i + 1] - xp[i]);
            return fp[i] + t * (fp[i + 1] - fp[i]);
        }
    }
    return fp[4];
}

/* --------------------------------------------------------------------------
 * 5. PianoSoundboard Kernel (Modal Plate + 6 Sparse Waveguides + Dispersion)
 * -------------------------------------------------------------------------- */

#define NOVA_SB_BRIDGE_CH 5
#define NOVA_SB_MAX_MODES 64
#define NOVA_SB_MAX_ALLPASS 16
#define NOVA_SB_NUM_WG 6
#define NOVA_SB_NUM_TAPS 4
#define NOVA_SB_RAD_SECTIONS 2
#define NOVA_SB_SYMP_BP_SECTIONS 2
#define NOVA_SB_CHEST_SECTIONS 2
#define NOVA_SB_BODY_LP_SECTIONS 1

typedef struct NovaSbSos1 {
    double b0, b1, b2, a1, a2;
    double z0, z1;
} NovaSbSos1;

typedef struct NovaSbSos2 {
    double b0, b1, b2, a1, a2;
    double z_l[2];
    double z_r[2];
} NovaSbSos2;

typedef struct NovaSbSos5 {
    double b0, b1, b2, a1, a2;
    double z[NOVA_SB_BRIDGE_CH][2];
} NovaSbSos5;

typedef struct NovaSbWaveguide {
    int32_t delay;
    int32_t ring_len;   /* delay - 1 */
    int32_t pos;
    double b0, b1, a1, aD;
    double z0;
    double* ring;
} NovaSbWaveguide;

typedef struct NovaPianoSoundboard {
    int32_t sample_rate;
    int32_t num_modes;
    int32_t num_ap_l;
    int32_t num_ap_r;
    int32_t max_delay;
    int32_t body_pos;
    int32_t last_res_pct;

    double plate_hz[NOVA_SB_MAX_MODES];
    double bridge_modes[NOVA_SB_BRIDGE_CH][NOVA_SB_MAX_MODES];
    double mode_pressure_l[NOVA_SB_MAX_MODES];
    double mode_pressure_r[NOVA_SB_MAX_MODES];

    double modal_g[NOVA_SB_MAX_MODES];
    double modal_a1[NOVA_SB_MAX_MODES];
    double modal_a2[NOVA_SB_MAX_MODES];
    double modal_z0[NOVA_SB_MAX_MODES];
    double modal_z1[NOVA_SB_MAX_MODES];

    NovaSbSos5 rad[NOVA_SB_RAD_SECTIONS];
    NovaSbSos1 symp_bp[NOVA_SB_SYMP_BP_SECTIONS];
    NovaSbSos2 chest[NOVA_SB_CHEST_SECTIONS];
    NovaSbSos1 ap_l[NOVA_SB_MAX_ALLPASS];
    NovaSbSos1 ap_r[NOVA_SB_MAX_ALLPASS];
    NovaSbSos2 body_lp[NOVA_SB_BODY_LP_SECTIONS];

    NovaSbWaveguide wg[NOVA_SB_NUM_WG];
    double* wg_pool;

    int32_t tap_delays[2][NOVA_SB_NUM_TAPS];
    double* body_hist;
} NovaPianoSoundboard;

static void nova_sb_load_sos1(NovaSbSos1* s, const double* row) {
    double a0 = (fabs(row[3]) > 1e-12) ? row[3] : 1.0;
    s->b0 = row[0] / a0;
    s->b1 = row[1] / a0;
    s->b2 = row[2] / a0;
    s->a1 = row[4] / a0;
    s->a2 = row[5] / a0;
    s->z0 = 0.0;
    s->z1 = 0.0;
}

static void nova_sb_load_sos2(NovaSbSos2* s, const double* row) {
    double a0 = (fabs(row[3]) > 1e-12) ? row[3] : 1.0;
    s->b0 = row[0] / a0;
    s->b1 = row[1] / a0;
    s->b2 = row[2] / a0;
    s->a1 = row[4] / a0;
    s->a2 = row[5] / a0;
    s->z_l[0] = 0.0; s->z_l[1] = 0.0;
    s->z_r[0] = 0.0; s->z_r[1] = 0.0;
}

static void nova_sb_load_sos5(NovaSbSos5* s, const double* row) {
    int ch;
    double a0 = (fabs(row[3]) > 1e-12) ? row[3] : 1.0;
    s->b0 = row[0] / a0;
    s->b1 = row[1] / a0;
    s->b2 = row[2] / a0;
    s->a1 = row[4] / a0;
    s->a2 = row[5] / a0;
    for (ch = 0; ch < NOVA_SB_BRIDGE_CH; ++ch) {
        s->z[ch][0] = 0.0;
        s->z[ch][1] = 0.0;
    }
}

static double nova_sb_sos1_step(NovaSbSos1* s, double x) {
    double y = s->b0 * x + s->z0;
    s->z0 = s->b1 * x - s->a1 * y + s->z1;
    s->z1 = s->b2 * x - s->a2 * y;
    return y;
}

static void nova_sb_update_res_coeffs(NovaPianoSoundboard* ctx, int32_t res_pct) {
    static const double t60_xp[5] = {0.0, 0.45, 0.67, 0.78, 1.0};
    static const double t60_fp[5] = {0.03, 0.10, 0.16, 0.85, 2.05};
    double res = (double)res_pct * 0.01;
    double sr = (double)ctx->sample_rate;
    double low_db, high_db, gl, kl, gh, kh;
    double base_loss, freq_loss, wood_coef, cutoff_hz, plate_scale;
    double t60_symp, denom_t60, damp_pole;
    int32_t m, w;

    /* 0. Chest tilt shelves */
    low_db = nova_interp3(res, 0.0, 0.67, 1.0, -5.5, 0.0, 5.0);
    high_db = nova_interp3(res, 0.0, 0.67, 1.0, 2.5, 0.0, -1.8);
    gl = pow(10.0, low_db / 20.0);
    kl = tan(M_PI * ((320.0 < 0.25 * sr) ? 320.0 : (0.25 * sr)) / sr);
    ctx->chest[0].b0 = (1.0 + gl * kl) / (1.0 + kl);
    ctx->chest[0].b1 = (gl * kl - 1.0) / (1.0 + kl);
    ctx->chest[0].b2 = 0.0;
    ctx->chest[0].a1 = (kl - 1.0) / (1.0 + kl);
    ctx->chest[0].a2 = 0.0;

    gh = pow(10.0, high_db / 20.0);
    kh = tan(M_PI * ((950.0 < 0.35 * sr) ? 950.0 : (0.35 * sr)) / sr);
    ctx->chest[1].b0 = (gh + kh) / (1.0 + kh);
    ctx->chest[1].b1 = (kh - gh) / (1.0 + kh);
    ctx->chest[1].b2 = 0.0;
    ctx->chest[1].a1 = (kh - 1.0) / (1.0 + kh);
    ctx->chest[1].a2 = 0.0;

    /* 1. Modal resonators */
    base_loss = nova_interp3(res, 0.0, 0.67, 1.0, 72.0, 28.0, 11.5);
    freq_loss = nova_interp3(res, 0.0, 0.67, 1.0, 0.195, 0.110, 0.048);
    wood_coef = nova_interp3(res, 0.0, 0.67, 1.0, -0.50, 0.0, 0.85);
    cutoff_hz = nova_interp3(res, 0.0, 0.67, 1.0, 360.0, 580.0, 760.0);
    plate_scale = nova_interp3(res, 0.0, 0.67, 1.0, 0.14, 1.05, 1.32);

    for (m = 0; m < ctx->num_modes; ++m) {
        double f_hz = ctx->plate_hz[m];
        double loss_p = base_loss + freq_loss * f_hz;
        double r_p = exp(-loss_p / sr);
        double theta_p = 2.0 * M_PI * f_hz / sr;
        double dw = (f_hz - 165.0) / 185.0;
        double wood_boost = 1.0 + wood_coef * exp(-(dw * dw));
        double f_over_c = f_hz / cutoff_hz;
        double rad_eff = (1.0 - exp(-f_hz / 82.0)) / sqrt(1.0 + f_over_c * f_over_c);
        ctx->modal_g[m] = plate_scale * wood_boost * (1.0 - r_p * r_p) * rad_eff;
        ctx->modal_a1[m] = -2.0 * r_p * cos(theta_p);
        ctx->modal_a2[m] = r_p * r_p;
    }

    /* 2. Sympathetic waveguides */
    t60_symp = nova_interp5(res, t60_xp, t60_fp);
    denom_t60 = (t60_symp > 0.02) ? t60_symp : 0.02;
    damp_pole = pow(0.24, 44100.0 / sr);
    for (w = 0; w < NOVA_SB_NUM_WG; ++w) {
        double delay_sec = (double)ctx->wg[w].delay / sr;
        double loop_gain = pow(10.0, -3.0 * delay_sec / denom_t60);
        double one_minus_lg2;
        double norm;
        if (loop_gain > 0.990) loop_gain = 0.990;
        one_minus_lg2 = 1.0 - loop_gain * loop_gain;
        if (one_minus_lg2 < 1e-6) one_minus_lg2 = 1e-6;
        norm = sqrt(one_minus_lg2);
        ctx->wg[w].b0 = norm;
        ctx->wg[w].b1 = -norm * damp_pole;
        ctx->wg[w].a1 = -damp_pole;
        ctx->wg[w].aD = -loop_gain * (1.0 - damp_pole);
    }

    ctx->last_res_pct = res_pct;
}

NOVA_API NovaPianoSoundboard* NOVA_CALL nova_sb_create(
    int32_t sample_rate,
    int32_t num_modes,
    const double* plate_hz,
    const double* bridge_modes,
    const double* mode_pressure,
    const double* rad_sos,
    const double* symp_bp_sos,
    const double* body_lp_sos,
    int32_t num_ap_l,
    const double* ap_l_sos,
    int32_t num_ap_r,
    const double* ap_r_sos,
    const int32_t* tap_delays,
    int32_t max_delay,
    const int32_t* wg_delays
) {
    NovaPianoSoundboard* ctx;
    int32_t i, ch;
    size_t total_wg = 0;
    double* wg_ptr;

    if (sample_rate < 8000 ||
        num_modes < 1 || num_modes > NOVA_SB_MAX_MODES ||
        num_ap_l < 1 || num_ap_l > NOVA_SB_MAX_ALLPASS ||
        num_ap_r < 1 || num_ap_r > NOVA_SB_MAX_ALLPASS ||
        max_delay < 1 ||
        !plate_hz || !bridge_modes || !mode_pressure ||
        !rad_sos || !symp_bp_sos || !body_lp_sos ||
        !ap_l_sos || !ap_r_sos || !tap_delays || !wg_delays) {
        return NULL;
    }

    for (i = 0; i < NOVA_SB_NUM_WG; ++i) {
        if (wg_delays[i] < 4) {
            return NULL;
        }
        total_wg += (size_t)(wg_delays[i] - 1);
    }
    for (i = 0; i < 2 * NOVA_SB_NUM_TAPS; ++i) {
        if (tap_delays[i] < 1 || tap_delays[i] > max_delay) {
            return NULL;
        }
    }

    ctx = (NovaPianoSoundboard*)calloc(1, sizeof(NovaPianoSoundboard));
    if (!ctx) {
        return NULL;
    }

    ctx->wg_pool = (double*)calloc(total_wg, sizeof(double));
    ctx->body_hist = (double*)calloc((size_t)max_delay * 2U, sizeof(double));
    if (!ctx->wg_pool || !ctx->body_hist) {
        free(ctx->wg_pool);
        free(ctx->body_hist);
        free(ctx);
        return NULL;
    }

    ctx->sample_rate = sample_rate;
    ctx->num_modes = num_modes;
    ctx->num_ap_l = num_ap_l;
    ctx->num_ap_r = num_ap_r;
    ctx->max_delay = max_delay;
    ctx->body_pos = 0;
    ctx->last_res_pct = -1;

    for (i = 0; i < num_modes; ++i) {
        ctx->plate_hz[i] = plate_hz[i];
        ctx->mode_pressure_l[i] = mode_pressure[i * 2];
        ctx->mode_pressure_r[i] = mode_pressure[i * 2 + 1];
        for (ch = 0; ch < NOVA_SB_BRIDGE_CH; ++ch) {
            ctx->bridge_modes[ch][i] = bridge_modes[ch * num_modes + i];
        }
    }

    for (i = 0; i < NOVA_SB_RAD_SECTIONS; ++i) {
        nova_sb_load_sos5(&ctx->rad[i], rad_sos + i * 6);
    }
    for (i = 0; i < NOVA_SB_SYMP_BP_SECTIONS; ++i) {
        nova_sb_load_sos1(&ctx->symp_bp[i], symp_bp_sos + i * 6);
    }
    for (i = 0; i < NOVA_SB_BODY_LP_SECTIONS; ++i) {
        nova_sb_load_sos2(&ctx->body_lp[i], body_lp_sos + i * 6);
    }
    for (i = 0; i < num_ap_l; ++i) {
        nova_sb_load_sos1(&ctx->ap_l[i], ap_l_sos + i * 6);
    }
    for (i = 0; i < num_ap_r; ++i) {
        nova_sb_load_sos1(&ctx->ap_r[i], ap_r_sos + i * 6);
    }

    for (ch = 0; ch < 2; ++ch) {
        for (i = 0; i < NOVA_SB_NUM_TAPS; ++i) {
            ctx->tap_delays[ch][i] = tap_delays[ch * NOVA_SB_NUM_TAPS + i];
        }
    }

    wg_ptr = ctx->wg_pool;
    for (i = 0; i < NOVA_SB_NUM_WG; ++i) {
        int32_t d = wg_delays[i];
        ctx->wg[i].delay = d;
        ctx->wg[i].ring_len = d - 1;
        ctx->wg[i].pos = 0;
        ctx->wg[i].z0 = 0.0;
        ctx->wg[i].ring = wg_ptr;
        wg_ptr += (size_t)(d - 1);
    }

    return ctx;
}

NOVA_API void NOVA_CALL nova_sb_destroy(NovaPianoSoundboard* ctx) {
    if (ctx) {
        free(ctx->wg_pool);
        free(ctx->body_hist);
        free(ctx);
    }
}

NOVA_API void NOVA_CALL nova_sb_reset(NovaPianoSoundboard* ctx) {
    int32_t i, ch;
    size_t total_wg = 0;
    if (!ctx) {
        return;
    }

    for (i = 0; i < NOVA_SB_MAX_MODES; ++i) {
        ctx->modal_z0[i] = 0.0;
        ctx->modal_z1[i] = 0.0;
    }
    for (i = 0; i < NOVA_SB_RAD_SECTIONS; ++i) {
        for (ch = 0; ch < NOVA_SB_BRIDGE_CH; ++ch) {
            ctx->rad[i].z[ch][0] = 0.0;
            ctx->rad[i].z[ch][1] = 0.0;
        }
    }
    for (i = 0; i < NOVA_SB_SYMP_BP_SECTIONS; ++i) {
        ctx->symp_bp[i].z0 = 0.0;
        ctx->symp_bp[i].z1 = 0.0;
    }
    for (i = 0; i < NOVA_SB_CHEST_SECTIONS; ++i) {
        ctx->chest[i].z_l[0] = 0.0; ctx->chest[i].z_l[1] = 0.0;
        ctx->chest[i].z_r[0] = 0.0; ctx->chest[i].z_r[1] = 0.0;
    }
    for (i = 0; i < ctx->num_ap_l; ++i) {
        ctx->ap_l[i].z0 = 0.0;
        ctx->ap_l[i].z1 = 0.0;
    }
    for (i = 0; i < ctx->num_ap_r; ++i) {
        ctx->ap_r[i].z0 = 0.0;
        ctx->ap_r[i].z1 = 0.0;
    }
    for (i = 0; i < NOVA_SB_BODY_LP_SECTIONS; ++i) {
        ctx->body_lp[i].z_l[0] = 0.0; ctx->body_lp[i].z_l[1] = 0.0;
        ctx->body_lp[i].z_r[0] = 0.0; ctx->body_lp[i].z_r[1] = 0.0;
    }
    for (i = 0; i < NOVA_SB_NUM_WG; ++i) {
        ctx->wg[i].pos = 0;
        ctx->wg[i].z0 = 0.0;
        total_wg += (size_t)ctx->wg[i].ring_len;
    }
    if (ctx->wg_pool && total_wg > 0) {
        memset(ctx->wg_pool, 0, total_wg * sizeof(double));
    }
    if (ctx->body_hist && ctx->max_delay > 0) {
        memset(ctx->body_hist, 0, (size_t)ctx->max_delay * 2U * sizeof(double));
    }
    ctx->body_pos = 0;
}

#define NOVA_SB_BLK 256

NOVA_API void NOVA_CALL nova_sb_process(
    NovaPianoSoundboard* ctx,
    const float* in_5ch,
    float* out_stereo,
    int32_t frames,
    double resonance
) {
    static const double DIFFUSE_PRESSURE[5][2] = {
        {0.80, 0.34}, {0.76, 0.50}, {0.67, 0.67}, {0.50, 0.76}, {0.34, 0.80}
    };
    static const double RESONATOR_STEREO[6][2] = {
        {0.82, 0.24}, {0.26, 0.80}, {0.74, 0.38},
        {0.36, 0.76}, {0.66, 0.48}, {0.46, 0.68}
    };
    static const double BODY_TAP_GAINS[2][4] = {
        {0.068, -0.052, 0.038, -0.026},
        {0.065, -0.054, 0.036, -0.028}
    };
    static const double symp_xp[5] = {0.0, 0.35, 0.67, 0.78, 1.0};
    static const double symp_fp[5] = {0.0, 0.015, 0.035, 0.38, 0.95};

    double res;
    int32_t res_pct;
    double symp_mix, direct_gain, modal_gain, lid_gain;
    int32_t n_modes, n_ap_l, n_ap_r, max_d, b_pos;
    int32_t offset, i, ch, m, w, s, t;

    double rad_b0[NOVA_SB_BRIDGE_CH][NOVA_SB_BLK];
    double mono_buf[NOVA_SB_BLK];
    double direct_l_buf[NOVA_SB_BLK];
    double direct_r_buf[NOVA_SB_BLK];
    double modal_l_buf[NOVA_SB_BLK];
    double modal_r_buf[NOVA_SB_BLK];
    double plate_l_buf[NOVA_SB_BLK];
    double plate_r_buf[NOVA_SB_BLK];

    if (!ctx || !in_5ch || !out_stereo || frames <= 0) {
        return;
    }

    res = isfinite(resonance) ? nova_clamp_d(resonance, 0.0, 1.0) : 0.0;
    res_pct = (int32_t)llrint(res * 100.0);
    if (res_pct != ctx->last_res_pct) {
        nova_sb_update_res_coeffs(ctx, res_pct);
    }

    symp_mix = nova_interp5(res, symp_xp, symp_fp);
    direct_gain = nova_interp3(res, 0.0, 0.67, 1.0, 0.82, 0.52, 0.34);
    modal_gain = nova_interp3(res, 0.0, 0.67, 1.0, 0.10, 0.71, 0.68);
    lid_gain = nova_interp3(res, 0.0, 0.67, 1.0, 0.04, 0.63, 1.35);

    n_modes = ctx->num_modes;
    n_ap_l = ctx->num_ap_l;
    n_ap_r = ctx->num_ap_r;
    max_d = ctx->max_delay;
    b_pos = ctx->body_pos;

    for (offset = 0; offset < frames; offset += NOVA_SB_BLK) {
        int32_t sub = frames - offset;
        const float* in_blk = in_5ch + offset * NOVA_SB_BRIDGE_CH;
        float* out_blk = out_stereo + offset * 2;
        if (sub > NOVA_SB_BLK) sub = NOVA_SB_BLK;

        /* 1. Sanitize & Radiation SOS (2 sections x 5 channels, register hoisted) */
        for (ch = 0; ch < NOVA_SB_BRIDGE_CH; ++ch) {
            double r0_b0 = ctx->rad[0].b0, r0_b1 = ctx->rad[0].b1, r0_b2 = ctx->rad[0].b2;
            double r0_a1 = ctx->rad[0].a1, r0_a2 = ctx->rad[0].a2;
            double r0_z0 = ctx->rad[0].z[ch][0], r0_z1 = ctx->rad[0].z[ch][1];
            double r1_b0 = ctx->rad[1].b0, r1_b1 = ctx->rad[1].b1, r1_b2 = ctx->rad[1].b2;
            double r1_a1 = ctx->rad[1].a1, r1_a2 = ctx->rad[1].a2;
            double r1_z0 = ctx->rad[1].z[ch][0], r1_z1 = ctx->rad[1].z[ch][1];
            double* dst = rad_b0[ch];
            for (i = 0; i < sub; ++i) {
                double x = nova_sanitize_d((double)in_blk[i * NOVA_SB_BRIDGE_CH + ch]);
                double y0 = r0_b0 * x + r0_z0;
                r0_z0 = r0_b1 * x - r0_a1 * y0 + r0_z1;
                r0_z1 = r0_b2 * x - r0_a2 * y0;
                double y1 = r1_b0 * y0 + r1_z0;
                r1_z0 = r1_b1 * y0 - r1_a1 * y1 + r1_z1;
                r1_z1 = r1_b2 * y0 - r1_a2 * y1;
                dst[i] = y1;
            }
            ctx->rad[0].z[ch][0] = r0_z0;
            ctx->rad[0].z[ch][1] = r0_z1;
            ctx->rad[1].z[ch][0] = r1_z0;
            ctx->rad[1].z[ch][1] = r1_z1;
        }

        for (i = 0; i < sub; ++i) {
            double f0 = rad_b0[0][i], f1 = rad_b0[1][i], f2 = rad_b0[2][i], f3 = rad_b0[3][i], f4 = rad_b0[4][i];
            direct_l_buf[i] = f0 * DIFFUSE_PRESSURE[0][0] + f1 * DIFFUSE_PRESSURE[1][0]
                            + f2 * DIFFUSE_PRESSURE[2][0] + f3 * DIFFUSE_PRESSURE[3][0] + f4 * DIFFUSE_PRESSURE[4][0];
            direct_r_buf[i] = f0 * DIFFUSE_PRESSURE[0][1] + f1 * DIFFUSE_PRESSURE[1][1]
                            + f2 * DIFFUSE_PRESSURE[2][1] + f3 * DIFFUSE_PRESSURE[3][1] + f4 * DIFFUSE_PRESSURE[4][1];
            mono_buf[i] = (f0 + f1 + f2 + f3 + f4) * 0.45;
            modal_l_buf[i] = 0.0;
            modal_r_buf[i] = 0.0;
        }

        /* 2. Modal Plate Resonators (4-way unrolled for ILP, m outer, i inner) */
        for (m = 0; m + 3 < n_modes; m += 4) {
            double bm0_0 = ctx->bridge_modes[0][m],     bm0_1 = ctx->bridge_modes[0][m + 1], bm0_2 = ctx->bridge_modes[0][m + 2], bm0_3 = ctx->bridge_modes[0][m + 3];
            double bm1_0 = ctx->bridge_modes[1][m],     bm1_1 = ctx->bridge_modes[1][m + 1], bm1_2 = ctx->bridge_modes[1][m + 2], bm1_3 = ctx->bridge_modes[1][m + 3];
            double bm2_0 = ctx->bridge_modes[2][m],     bm2_1 = ctx->bridge_modes[2][m + 1], bm2_2 = ctx->bridge_modes[2][m + 2], bm2_3 = ctx->bridge_modes[2][m + 3];
            double bm3_0 = ctx->bridge_modes[3][m],     bm3_1 = ctx->bridge_modes[3][m + 1], bm3_2 = ctx->bridge_modes[3][m + 2], bm3_3 = ctx->bridge_modes[3][m + 3];
            double bm4_0 = ctx->bridge_modes[4][m],     bm4_1 = ctx->bridge_modes[4][m + 1], bm4_2 = ctx->bridge_modes[4][m + 2], bm4_3 = ctx->bridge_modes[4][m + 3];
            double g0 = ctx->modal_g[m],   g1 = ctx->modal_g[m + 1],   g2 = ctx->modal_g[m + 2],   g3 = ctx->modal_g[m + 3];
            double a1_0 = ctx->modal_a1[m], a1_1 = ctx->modal_a1[m + 1], a1_2 = ctx->modal_a1[m + 2], a1_3 = ctx->modal_a1[m + 3];
            double a2_0 = ctx->modal_a2[m], a2_1 = ctx->modal_a2[m + 1], a2_2 = ctx->modal_a2[m + 2], a2_3 = ctx->modal_a2[m + 3];
            double z0_0 = ctx->modal_z0[m], z0_1 = ctx->modal_z0[m + 1], z0_2 = ctx->modal_z0[m + 2], z0_3 = ctx->modal_z0[m + 3];
            double z1_0 = ctx->modal_z1[m], z1_1 = ctx->modal_z1[m + 1], z1_2 = ctx->modal_z1[m + 2], z1_3 = ctx->modal_z1[m + 3];
            double mpl0 = ctx->mode_pressure_l[m], mpl1 = ctx->mode_pressure_l[m + 1], mpl2 = ctx->mode_pressure_l[m + 2], mpl3 = ctx->mode_pressure_l[m + 3];
            double mpr0 = ctx->mode_pressure_r[m], mpr1 = ctx->mode_pressure_r[m + 1], mpr2 = ctx->mode_pressure_r[m + 2], mpr3 = ctx->mode_pressure_r[m + 3];
            const double* r0 = rad_b0[0];
            const double* r1 = rad_b0[1];
            const double* r2 = rad_b0[2];
            const double* r3 = rad_b0[3];
            const double* r4 = rad_b0[4];

            for (i = 0; i < sub; ++i) {
                double f0 = r0[i], f1 = r1[i], f2 = r2[i], f3 = r3[i], f4 = r4[i];
                double gd0 = g0 * (f0 * bm0_0 + f1 * bm1_0 + f2 * bm2_0 + f3 * bm3_0 + f4 * bm4_0);
                double gd1 = g1 * (f0 * bm0_1 + f1 * bm1_1 + f2 * bm2_1 + f3 * bm3_1 + f4 * bm4_1);
                double gd2 = g2 * (f0 * bm0_2 + f1 * bm1_2 + f2 * bm2_2 + f3 * bm3_2 + f4 * bm4_2);
                double gd3 = g3 * (f0 * bm0_3 + f1 * bm1_3 + f2 * bm2_3 + f3 * bm3_3 + f4 * bm4_3);

                double resp0 = gd0 + z0_0;
                double resp1 = gd1 + z0_1;
                double resp2 = gd2 + z0_2;
                double resp3 = gd3 + z0_3;

                z0_0 = -a1_0 * resp0 + z1_0;
                z0_1 = -a1_1 * resp1 + z1_1;
                z0_2 = -a1_2 * resp2 + z1_2;
                z0_3 = -a1_3 * resp3 + z1_3;

                z1_0 = -gd0 - a2_0 * resp0;
                z1_1 = -gd1 - a2_1 * resp1;
                z1_2 = -gd2 - a2_2 * resp2;
                z1_3 = -gd3 - a2_3 * resp3;

                modal_l_buf[i] += resp0 * mpl0 + resp1 * mpl1 + resp2 * mpl2 + resp3 * mpl3;
                modal_r_buf[i] += resp0 * mpr0 + resp1 * mpr1 + resp2 * mpr2 + resp3 * mpr3;
            }
            ctx->modal_z0[m]     = z0_0; ctx->modal_z0[m + 1] = z0_1; ctx->modal_z0[m + 2] = z0_2; ctx->modal_z0[m + 3] = z0_3;
            ctx->modal_z1[m]     = z1_0; ctx->modal_z1[m + 1] = z1_1; ctx->modal_z1[m + 2] = z1_2; ctx->modal_z1[m + 3] = z1_3;
        }
        for (; m < n_modes; ++m) {
            double bm0 = ctx->bridge_modes[0][m];
            double bm1 = ctx->bridge_modes[1][m];
            double bm2 = ctx->bridge_modes[2][m];
            double bm3 = ctx->bridge_modes[3][m];
            double bm4 = ctx->bridge_modes[4][m];
            double g = ctx->modal_g[m];
            double a1 = ctx->modal_a1[m];
            double a2 = ctx->modal_a2[m];
            double z0 = ctx->modal_z0[m];
            double z1 = ctx->modal_z1[m];
            double mpl = ctx->mode_pressure_l[m];
            double mpr = ctx->mode_pressure_r[m];
            const double* r0 = rad_b0[0];
            const double* r1 = rad_b0[1];
            const double* r2 = rad_b0[2];
            const double* r3 = rad_b0[3];
            const double* r4 = rad_b0[4];

            for (i = 0; i < sub; ++i) {
                double drive = r0[i] * bm0 + r1[i] * bm1 + r2[i] * bm2 + r3[i] * bm3 + r4[i] * bm4;
                double gd = g * drive;
                double resp = gd + z0;
                z0 = -a1 * resp + z1;
                z1 = -gd - a2 * resp;
                modal_l_buf[i] += resp * mpl;
                modal_r_buf[i] += resp * mpr;
            }
            ctx->modal_z0[m] = z0;
            ctx->modal_z1[m] = z1;
        }

        /* 3. Sympathetic Bandpass (2 SOS1 sections, hoisted) */
        for (s = 0; s < NOVA_SB_SYMP_BP_SECTIONS; ++s) {
            double b0 = ctx->symp_bp[s].b0, b1 = ctx->symp_bp[s].b1, b2 = ctx->symp_bp[s].b2;
            double a1 = ctx->symp_bp[s].a1, a2 = ctx->symp_bp[s].a2;
            double z0 = ctx->symp_bp[s].z0, z1 = ctx->symp_bp[s].z1;
            for (i = 0; i < sub; ++i) {
                double x = mono_buf[i];
                double y = b0 * x + z0;
                z0 = b1 * x - a1 * y + z1;
                z1 = b2 * x - a2 * y;
                mono_buf[i] = y;
            }
            ctx->symp_bp[s].z0 = z0;
            ctx->symp_bp[s].z1 = z1;
        }

        for (i = 0; i < sub; ++i) {
            plate_l_buf[i] = direct_gain * direct_l_buf[i] + modal_gain * modal_l_buf[i];
            plate_r_buf[i] = direct_gain * direct_r_buf[i] + modal_gain * modal_r_buf[i];
        }

        /* 4. 6 O(1) Sparse Waveguides (w outer, i inner) */
        for (w = 0; w < NOVA_SB_NUM_WG; ++w) {
            NovaSbWaveguide* wg = &ctx->wg[w];
            double b0 = wg->b0, b1 = wg->b1, a1 = wg->a1, aD = wg->aD, z0 = wg->z0;
            double* ring = wg->ring;
            int32_t rlen = wg->ring_len, pos = wg->pos;
            if (symp_mix > 0.0) {
                double sl_gain = symp_mix * RESONATOR_STEREO[w][0];
                double sr_gain = symp_mix * RESONATOR_STEREO[w][1];
                for (i = 0; i < sub; ++i) {
                    double sd = mono_buf[i];
                    double wg_tail = z0;
                    double wg_y = b0 * sd + wg_tail;
                    double delayed = ring[pos];
                    z0 = b1 * sd - a1 * wg_y + delayed;
                    ring[pos] = nova_flush_denormal_d(-aD * wg_y);
                    pos++;
                    if (pos >= rlen) pos = 0;
                    plate_l_buf[i] += wg_tail * sl_gain;
                    plate_r_buf[i] += wg_tail * sr_gain;
                }
            } else {
                for (i = 0; i < sub; ++i) {
                    double sd = mono_buf[i];
                    double wg_y = b0 * sd + z0;
                    double delayed = ring[pos];
                    z0 = b1 * sd - a1 * wg_y + delayed;
                    ring[pos] = nova_flush_denormal_d(-aD * wg_y);
                    pos++;
                    if (pos >= rlen) pos = 0;
                }
            }
            wg->z0 = z0;
            wg->pos = pos;
        }

        /* 5. Chest Tilt SOS (2 sections) */
        for (s = 0; s < NOVA_SB_CHEST_SECTIONS; ++s) {
            double b0 = ctx->chest[s].b0, b1 = ctx->chest[s].b1, b2 = ctx->chest[s].b2;
            double a1 = ctx->chest[s].a1, a2 = ctx->chest[s].a2;
            double zl0 = ctx->chest[s].z_l[0], zl1 = ctx->chest[s].z_l[1];
            double zr0 = ctx->chest[s].z_r[0], zr1 = ctx->chest[s].z_r[1];
            for (i = 0; i < sub; ++i) {
                double xl = plate_l_buf[i], xr = plate_r_buf[i];
                double yl = b0 * xl + zl0;
                zl0 = b1 * xl - a1 * yl + zl1;
                zl1 = b2 * xl - a2 * yl;
                plate_l_buf[i] = yl;

                double yr = b0 * xr + zr0;
                zr0 = b1 * xr - a1 * yr + zr1;
                zr1 = b2 * xr - a2 * yr;
                plate_r_buf[i] = yr;
            }
            ctx->chest[s].z_l[0] = zl0; ctx->chest[s].z_l[1] = zl1;
            ctx->chest[s].z_r[0] = zr0; ctx->chest[s].z_r[1] = zr1;
        }

        /* 6. Allpass Dispersion (paired L & R for ILP) */
        {
            int32_t n_ap_min = (n_ap_l < n_ap_r) ? n_ap_l : n_ap_r;
            for (s = 0; s < n_ap_min; ++s) {
                double bl0 = ctx->ap_l[s].b0, bl1 = ctx->ap_l[s].b1, bl2 = ctx->ap_l[s].b2;
                double al1 = ctx->ap_l[s].a1, al2 = ctx->ap_l[s].a2;
                double zl0 = ctx->ap_l[s].z0, zl1 = ctx->ap_l[s].z1;
                double br0 = ctx->ap_r[s].b0, br1 = ctx->ap_r[s].b1, br2 = ctx->ap_r[s].b2;
                double ar1 = ctx->ap_r[s].a1, ar2 = ctx->ap_r[s].a2;
                double zr0 = ctx->ap_r[s].z0, zr1 = ctx->ap_r[s].z1;
                for (i = 0; i < sub; ++i) {
                    double xl = plate_l_buf[i];
                    double xr = plate_r_buf[i];
                    double yl = bl0 * xl + zl0;
                    double yr = br0 * xr + zr0;
                    zl0 = bl1 * xl - al1 * yl + zl1;
                    zr0 = br1 * xr - ar1 * yr + zr1;
                    zl1 = bl2 * xl - al2 * yl;
                    zr1 = br2 * xr - ar2 * yr;
                    plate_l_buf[i] = yl;
                    plate_r_buf[i] = yr;
                }
                ctx->ap_l[s].z0 = zl0; ctx->ap_l[s].z1 = zl1;
                ctx->ap_r[s].z0 = zr0; ctx->ap_r[s].z1 = zr1;
            }
            for (s = n_ap_min; s < n_ap_l; ++s) {
                double b0 = ctx->ap_l[s].b0, b1 = ctx->ap_l[s].b1, b2 = ctx->ap_l[s].b2;
                double a1 = ctx->ap_l[s].a1, a2 = ctx->ap_l[s].a2;
                double z0 = ctx->ap_l[s].z0, z1 = ctx->ap_l[s].z1;
                for (i = 0; i < sub; ++i) {
                    double x = plate_l_buf[i];
                    double y = b0 * x + z0;
                    z0 = b1 * x - a1 * y + z1;
                    z1 = b2 * x - a2 * y;
                    plate_l_buf[i] = y;
                }
                ctx->ap_l[s].z0 = z0; ctx->ap_l[s].z1 = z1;
            }
            for (s = n_ap_min; s < n_ap_r; ++s) {
                double b0 = ctx->ap_r[s].b0, b1 = ctx->ap_r[s].b1, b2 = ctx->ap_r[s].b2;
                double a1 = ctx->ap_r[s].a1, a2 = ctx->ap_r[s].a2;
                double z0 = ctx->ap_r[s].z0, z1 = ctx->ap_r[s].z1;
                for (i = 0; i < sub; ++i) {
                    double x = plate_r_buf[i];
                    double y = b0 * x + z0;
                    z0 = b1 * x - a1 * y + z1;
                    z1 = b2 * x - a2 * y;
                    plate_r_buf[i] = y;
                }
                ctx->ap_r[s].z0 = z0; ctx->ap_r[s].z1 = z1;
            }
        }

        /* 7. Early Body LP + 4-Tap Stereo Reflections */
        {
            double b0 = ctx->body_lp[0].b0, b1 = ctx->body_lp[0].b1, b2 = ctx->body_lp[0].b2;
            double a1 = ctx->body_lp[0].a1, a2 = ctx->body_lp[0].a2;
            double zl0 = ctx->body_lp[0].z_l[0], zl1 = ctx->body_lp[0].z_l[1];
            double zr0 = ctx->body_lp[0].z_r[0], zr1 = ctx->body_lp[0].z_r[1];
            for (i = 0; i < sub; ++i) {
                double pl = plate_l_buf[i], pr = plate_r_buf[i];
                double flt_l = b0 * pl + zl0;
                double flt_r = b0 * pr + zr0;
                double early_l = 0.0, early_r = 0.0;
                zl0 = b1 * pl - a1 * flt_l + zl1;
                zl1 = b2 * pl - a2 * flt_l;
                zr0 = b1 * pr - a1 * flt_r + zr1;
                zr1 = b2 * pr - a2 * flt_r;

                for (t = 0; t < NOVA_SB_NUM_TAPS; ++t) {
                    int32_t idx_l = b_pos - ctx->tap_delays[0][t];
                    int32_t idx_r = b_pos - ctx->tap_delays[1][t];
                    if (idx_l < 0) idx_l += max_d;
                    if (idx_r < 0) idx_r += max_d;
                    early_l += BODY_TAP_GAINS[0][t] * ctx->body_hist[idx_l * 2];
                    early_r += BODY_TAP_GAINS[1][t] * ctx->body_hist[idx_r * 2 + 1];
                }

                ctx->body_hist[b_pos * 2] = nova_flush_denormal_d(flt_l);
                ctx->body_hist[b_pos * 2 + 1] = nova_flush_denormal_d(flt_r);
                b_pos++;
                if (b_pos >= max_d) b_pos = 0;

                out_blk[i * 2] = nova_sanitize_f32_out(pl + lid_gain * early_l);
                out_blk[i * 2 + 1] = nova_sanitize_f32_out(pr + lid_gain * early_r);
            }
            ctx->body_lp[0].z_l[0] = zl0; ctx->body_lp[0].z_l[1] = zl1;
            ctx->body_lp[0].z_r[0] = zr0; ctx->body_lp[0].z_r[1] = zr1;
        }
    }

    ctx->body_pos = b_pos;

    /* Flush denormals on scalar filter states */
    for (s = 0; s < NOVA_SB_RAD_SECTIONS; ++s) {
        for (ch = 0; ch < NOVA_SB_BRIDGE_CH; ++ch) {
            ctx->rad[s].z[ch][0] = nova_flush_denormal_d(ctx->rad[s].z[ch][0]);
            ctx->rad[s].z[ch][1] = nova_flush_denormal_d(ctx->rad[s].z[ch][1]);
        }
    }
    for (m = 0; m < n_modes; ++m) {
        ctx->modal_z0[m] = nova_flush_denormal_d(ctx->modal_z0[m]);
        ctx->modal_z1[m] = nova_flush_denormal_d(ctx->modal_z1[m]);
    }
    for (s = 0; s < NOVA_SB_SYMP_BP_SECTIONS; ++s) {
        ctx->symp_bp[s].z0 = nova_flush_denormal_d(ctx->symp_bp[s].z0);
        ctx->symp_bp[s].z1 = nova_flush_denormal_d(ctx->symp_bp[s].z1);
    }
    for (w = 0; w < NOVA_SB_NUM_WG; ++w) {
        ctx->wg[w].z0 = nova_flush_denormal_d(ctx->wg[w].z0);
    }
    for (s = 0; s < NOVA_SB_CHEST_SECTIONS; ++s) {
        ctx->chest[s].z_l[0] = nova_flush_denormal_d(ctx->chest[s].z_l[0]);
        ctx->chest[s].z_l[1] = nova_flush_denormal_d(ctx->chest[s].z_l[1]);
        ctx->chest[s].z_r[0] = nova_flush_denormal_d(ctx->chest[s].z_r[0]);
        ctx->chest[s].z_r[1] = nova_flush_denormal_d(ctx->chest[s].z_r[1]);
    }
    for (s = 0; s < n_ap_l; ++s) {
        ctx->ap_l[s].z0 = nova_flush_denormal_d(ctx->ap_l[s].z0);
        ctx->ap_l[s].z1 = nova_flush_denormal_d(ctx->ap_l[s].z1);
    }
    for (s = 0; s < n_ap_r; ++s) {
        ctx->ap_r[s].z0 = nova_flush_denormal_d(ctx->ap_r[s].z0);
        ctx->ap_r[s].z1 = nova_flush_denormal_d(ctx->ap_r[s].z1);
    }
    for (s = 0; s < NOVA_SB_BODY_LP_SECTIONS; ++s) {
        ctx->body_lp[s].z_l[0] = nova_flush_denormal_d(ctx->body_lp[s].z_l[0]);
        ctx->body_lp[s].z_l[1] = nova_flush_denormal_d(ctx->body_lp[s].z_l[1]);
        ctx->body_lp[s].z_r[0] = nova_flush_denormal_d(ctx->body_lp[s].z_r[0]);
        ctx->body_lp[s].z_r[1] = nova_flush_denormal_d(ctx->body_lp[s].z_r[1]);
    }
}

NOVA_API void NOVA_CALL nova_piano_eval_positions(
    const double* poles_c128,
    const double* state_c128,
    int32_t num_modes,
    int32_t n_blocks,
    double elapsed_sec,
    double block_dt_sec,
    float* out_positions_c64
) {
    int32_t m, b;
    if (!poles_c128 || !state_c128 || !out_positions_c64 || num_modes <= 0 || n_blocks <= 0) {
        return;
    }
    for (m = 0; m < num_modes; ++m) {
        double pr = poles_c128[2 * m];
        double pi = poles_c128[2 * m + 1];
        double sr = state_c128[2 * m];
        double si = state_c128[2 * m + 1];

        double e0 = exp(pr * elapsed_sec);
        double ang0 = pi * elapsed_sec;
        double e0_r = e0 * cos(ang0);
        double e0_i = e0 * sin(ang0);
        double cur_r = sr * e0_r - si * e0_i;
        double cur_i = sr * e0_i + si * e0_r;

        double es = exp(pr * block_dt_sec);
        double angs = pi * block_dt_sec;
        double step_r = es * cos(angs);
        double step_i = es * sin(angs);

        for (b = 0; b < n_blocks; ++b) {
            int32_t idx = (b * num_modes + m) * 2;
            double nr, ni;
            out_positions_c64[idx] = (float)cur_r;
            out_positions_c64[idx + 1] = (float)cur_i;
            nr = cur_r * step_r - cur_i * step_i;
            ni = cur_r * step_i + cur_i * step_r;
            cur_r = nr;
            cur_i = ni;
        }
    }
}

/* --------------------------------------------------------------------------
 * 6. Guitar Kernels (WaveguideStringPlane + Body/Pickup + Stereo Widener)
 * -------------------------------------------------------------------------- */

typedef struct NovaGuitarPlane {
    int32_t n_int;
    int32_t ring_pos;
    double* ring;
    double b_sus[3], a_sus[4];
    double b_rel[3], a_rel[4];
    double zi_loop[3];
    double zi_dc;
    int32_t in_release;
} NovaGuitarPlane;

NOVA_API NovaGuitarPlane* NOVA_CALL nova_guitar_plane_create(
    int32_t n_int,
    const double* b_sus3,
    const double* a_sus4,
    const double* b_rel3,
    const double* a_rel4
) {
    NovaGuitarPlane* ctx;
    double a0_s, a0_r;
    int i;
    if (n_int < 1 || !b_sus3 || !a_sus4 || !b_rel3 || !a_rel4) {
        return NULL;
    }
    ctx = (NovaGuitarPlane*)calloc(1, sizeof(NovaGuitarPlane));
    if (!ctx) {
        return NULL;
    }
    ctx->ring = (double*)calloc((size_t)n_int, sizeof(double));
    if (!ctx->ring) {
        free(ctx);
        return NULL;
    }
    ctx->n_int = n_int;
    a0_s = (fabs(a_sus4[0]) > 1e-12) ? a_sus4[0] : 1.0;
    a0_r = (fabs(a_rel4[0]) > 1e-12) ? a_rel4[0] : 1.0;
    for (i = 0; i < 3; ++i) {
        ctx->b_sus[i] = b_sus3[i] / a0_s;
        ctx->b_rel[i] = b_rel3[i] / a0_r;
    }
    for (i = 0; i < 4; ++i) {
        ctx->a_sus[i] = a_sus4[i] / a0_s;
        ctx->a_rel[i] = a_rel4[i] / a0_r;
    }
    return ctx;
}

NOVA_API void NOVA_CALL nova_guitar_plane_destroy(NovaGuitarPlane* ctx) {
    if (ctx) {
        free(ctx->ring);
        free(ctx);
    }
}

NOVA_API void NOVA_CALL nova_guitar_plane_reset(NovaGuitarPlane* ctx) {
    if (!ctx) {
        return;
    }
    if (ctx->ring && ctx->n_int > 0) {
        memset(ctx->ring, 0, (size_t)ctx->n_int * sizeof(double));
    }
    ctx->ring_pos = 0;
    ctx->zi_loop[0] = 0.0;
    ctx->zi_loop[1] = 0.0;
    ctx->zi_loop[2] = 0.0;
    ctx->zi_dc = 0.0;
    ctx->in_release = 0;
}

static void nova_guitar_plane_step_scaled(
    NovaGuitarPlane* ctx,
    const double* exc_in,
    double exc_scale,
    double* out_mono,
    int32_t frames,
    int32_t is_released
) {
    int32_t i, n_int, pos;
    double b0, b1, b2, a1, a2, a3;
    double z0, z1, z2, z_dc;
    double* ring;

    if (!ctx || frames <= 0) {
        return;
    }
    if (is_released && !ctx->in_release) {
        ctx->in_release = 1;
    }
    if (ctx->in_release) {
        b0 = ctx->b_rel[0]; b1 = ctx->b_rel[1]; b2 = ctx->b_rel[2];
        a1 = ctx->a_rel[1]; a2 = ctx->a_rel[2]; a3 = ctx->a_rel[3];
    } else {
        b0 = ctx->b_sus[0]; b1 = ctx->b_sus[1]; b2 = ctx->b_sus[2];
        a1 = ctx->a_sus[1]; a2 = ctx->a_sus[2]; a3 = ctx->a_sus[3];
    }

    n_int = ctx->n_int;
    pos = ctx->ring_pos;
    ring = ctx->ring;
    z0 = ctx->zi_loop[0];
    z1 = ctx->zi_loop[1];
    z2 = ctx->zi_loop[2];
    z_dc = ctx->zi_dc;

    for (i = 0; i < frames; ++i) {
        double delayed = ring[pos];
        double flt = b0 * delayed + z0;
        double x_exc = exc_in ? (nova_sanitize_d(exc_in[i]) * exc_scale) : 0.0;
        double new_s, y_dc;

        z0 = b1 * delayed - a1 * flt + z1;
        z1 = b2 * delayed - a2 * flt + z2;
        z2 = -a3 * flt;

        new_s = x_exc + flt;
        ring[pos] = nova_flush_denormal_d(new_s);
        pos++;
        if (pos >= n_int) pos = 0;

        y_dc = new_s + z_dc;
        z_dc = -new_s + 0.995 * y_dc;
        if (out_mono) {
            out_mono[i] = y_dc;
        }
    }

    ctx->ring_pos = pos;
    ctx->zi_loop[0] = nova_flush_denormal_d(z0);
    ctx->zi_loop[1] = nova_flush_denormal_d(z1);
    ctx->zi_loop[2] = nova_flush_denormal_d(z2);
    ctx->zi_dc = nova_flush_denormal_d(z_dc);
}

NOVA_API void NOVA_CALL nova_guitar_plane_step(
    NovaGuitarPlane* ctx,
    const double* exc_in,
    double* out_mono,
    int32_t frames,
    int32_t is_released
) {
    nova_guitar_plane_step_scaled(ctx, exc_in, 1.0, out_mono, frames, is_released);
}

static void nova_guitar_voice_chunk(
    NovaGuitarPlane* plane_a,
    NovaGuitarPlane* plane_b,
    const double* exc_buf,
    int32_t exc_len,
    int32_t s_idx,
    int32_t count,
    double* out_mono,
    int32_t is_released
) {
    int32_t exc_avail = 0;
    int32_t tail_zeros;
    if (count <= 0) {
        return;
    }
    if (exc_buf && s_idx < exc_len) {
        exc_avail = exc_len - s_idx;
        if (exc_avail > count) exc_avail = count;
    }
    tail_zeros = count - exc_avail;

    if (!plane_b) {
        if (exc_avail > 0) {
            nova_guitar_plane_step_scaled(plane_a, exc_buf + s_idx, 1.0, out_mono, exc_avail, is_released);
        }
        if (tail_zeros > 0) {
            nova_guitar_plane_step_scaled(plane_a, NULL, 1.0, out_mono ? (out_mono + exc_avail) : NULL, tail_zeros, is_released);
        }
    } else {
        /* Dual polarization: process plane_a into out_mono (if non-NULL) and plane_b chunkwise */
        double tmp_b[256];
        int32_t pos = 0;
        while (pos < count) {
            int32_t step = count - pos;
            const double* seg_exc = NULL;
            if (step > 256) step = 256;
            if (pos < exc_avail) {
                if (pos + step > exc_avail) step = exc_avail - pos;
                seg_exc = exc_buf + s_idx + pos;
            }
            nova_guitar_plane_step_scaled(plane_a, seg_exc, 1.0, out_mono ? (out_mono + pos) : NULL, step, is_released);
            nova_guitar_plane_step_scaled(plane_b, seg_exc, 0.85, out_mono ? tmp_b : NULL, step, is_released);
            if (out_mono) {
                int32_t k;
                for (k = 0; k < step; ++k) {
                    out_mono[pos + k] = 0.72 * out_mono[pos + k] + 0.28 * tmp_b[k];
                }
            }
            pos += step;
        }
    }
}

NOVA_API void NOVA_CALL nova_guitar_voice_render(
    NovaGuitarPlane* plane_a,
    NovaGuitarPlane* plane_b,
    const double* exc_buf,
    int32_t exc_len,
    int32_t start_sample,
    int32_t note_dur_samples,
    double* out_mono,
    int32_t frames
) {
    int32_t end_sample;
    if (!plane_a || frames <= 0) {
        return;
    }
    end_sample = start_sample + frames;
    if (start_sample < note_dur_samples && note_dur_samples < end_sample) {
        int32_t part1 = note_dur_samples - start_sample;
        int32_t part2 = end_sample - note_dur_samples;
        nova_guitar_voice_chunk(plane_a, plane_b, exc_buf, exc_len, start_sample, part1, out_mono, 0);
        nova_guitar_voice_chunk(plane_a, plane_b, exc_buf, exc_len, note_dur_samples, part2, out_mono ? (out_mono + part1) : NULL, 1);
    } else {
        int32_t released = (start_sample >= note_dur_samples) ? 1 : 0;
        nova_guitar_voice_chunk(plane_a, plane_b, exc_buf, exc_len, start_sample, frames, out_mono, released);
    }
}

typedef struct NovaGuitarBodyState {
    int32_t cached_mode;
    int32_t cached_sr;
    int32_t body_res_milli;
    int32_t pickup_pos_milli;
    int32_t initialized;
    int32_t pickup_pos_idx;
    double pickup_buf[64];
    NovaBiquad filters[5];
    double gains[4];
} NovaGuitarBodyState;

NOVA_API void NOVA_CALL nova_guitar_body_reset(NovaGuitarBodyState* st) {
    int i;
    if (!st) {
        return;
    }
    for (i = 0; i < 5; ++i) {
        nova_biquad_reset(&st->filters[i]);
    }
    memset(st->pickup_buf, 0, sizeof(st->pickup_buf));
    st->pickup_pos_idx = 0;
}

static void nova_guitar_body_ensure(
    NovaGuitarBodyState* st,
    int32_t mode,
    double body_resonance,
    double pickup_position,
    int32_t sr
) {
    int32_t br_milli = (int32_t)llrint(body_resonance * 1000.0);
    int32_t pp_milli = (int32_t)llrint(pickup_position * 1000.0);
    int i;

    if (st->initialized &&
        st->cached_mode == mode &&
        st->cached_sr == sr &&
        st->body_res_milli == br_milli &&
        st->pickup_pos_milli == pp_milli) {
        return;
    }

    st->cached_mode = mode;
    st->cached_sr = sr;
    st->body_res_milli = br_milli;
    st->pickup_pos_milli = pp_milli;
    st->initialized = 1;

    for (i = 0; i < 5; ++i) {
        nova_biquad_reset(&st->filters[i]);
    }

    if (mode == 0 || mode == 1) {
        int is_nylon = (mode == 1);
        double air_f = is_nylon ? 104.0 : 96.0;
        double top_f = is_nylon ? 212.0 : 192.0;
        double dip_f = is_nylon ? 415.0 : 390.0;
        double wood_f = is_nylon ? 840.0 : 960.0;
        nova_biquad_set_bandpass(&st->filters[0], air_f, 5.2, sr);
        nova_biquad_set_bandpass(&st->filters[1], top_f, 6.0, sr);
        nova_biquad_set_bandpass(&st->filters[2], dip_f, 4.5, sr);
        nova_biquad_set_bandpass(&st->filters[3], wood_f, 3.8, sr);
        nova_biquad_set_highpass(&st->filters[4], 62.0, 0.707, sr);
        st->gains[0] = 0.58 * body_resonance;
        st->gains[1] = 0.52 * body_resonance;
        st->gains[2] = 0.30 * body_resonance;
        st->gains[3] = 0.22 * body_resonance;
    } else if (mode == 2) {
        nova_biquad_set_peaking(&st->filters[0], 140.0, 3.0 * body_resonance, 1.1, sr);
        nova_biquad_set_lowpass(&st->filters[1], 1850.0, 1.35, sr);
    } else {
        double pu = nova_clamp_d(pickup_position, 0.05, 0.45);
        double fc = nova_clamp_d(3900.0 - (pu - 0.08) * 4200.0, 1850.0, 4300.0);
        double q_rlc = 1.25 + 0.85 * body_resonance;
        nova_biquad_set_highpass(&st->filters[0], 75.0, 0.707, sr);
        nova_biquad_set_peaking(&st->filters[1], fc * 0.72, 2.5 * body_resonance, 1.4, sr);
        nova_biquad_set_lowpass(&st->filters[2], fc, q_rlc, sr);
    }
}

NOVA_API void NOVA_CALL nova_guitar_body_process(
    NovaGuitarBodyState* st,
    const double* in_mono,
    double* out_mono,
    int32_t frames,
    int32_t mode,
    double body_resonance,
    double pickup_position,
    int32_t sample_rate
) {
    int32_t sr, i;
    if (!st || !in_mono || !out_mono || frames <= 0) {
        return;
    }
    sr = (sample_rate < 8000) ? 8000 : sample_rate;
    nova_guitar_body_ensure(st, mode, body_resonance, pickup_position, sr);

    if (mode == 0 || mode == 1) {
        for (i = 0; i < frames; ++i) {
            double x = nova_sanitize_d(in_mono[i]);
            double b0 = nova_biquad_step_l(&st->filters[0], x) * st->gains[0];
            double b1 = nova_biquad_step_l(&st->filters[1], x) * st->gains[1];
            double b2 = nova_biquad_step_l(&st->filters[2], x) * st->gains[2];
            double b3 = nova_biquad_step_l(&st->filters[3], x) * st->gains[3];
            double sum = 0.65 * x + b0 + b1 + b2 + b3;
            out_mono[i] = nova_biquad_step_l(&st->filters[4], sum);
        }
        for (i = 0; i < 5; ++i) nova_biquad_flush(&st->filters[i]);
    } else {
        int32_t pu_samples = (int32_t)llrint(pickup_position * 0.0045 * (double)sr);
        int32_t n_flt = (mode == 2) ? 2 : 3;
        int32_t pos = st->pickup_pos_idx;
        if (pu_samples < 2) pu_samples = 2;
        if (pu_samples > 64) pu_samples = 64;
        if (pos >= pu_samples) pos = 0;

        for (i = 0; i < frames; ++i) {
            double x = nova_sanitize_d(in_mono[i]);
            double delayed = st->pickup_buf[pos];
            double y;
            int32_t f_idx;
            st->pickup_buf[pos] = nova_flush_denormal_d(x);
            pos++;
            if (pos >= pu_samples) pos = 0;

            y = 0.72 * x + 0.28 * (x - delayed);
            for (f_idx = 0; f_idx < n_flt; ++f_idx) {
                y = nova_biquad_step_l(&st->filters[f_idx], y);
            }
            out_mono[i] = y;
        }
        st->pickup_pos_idx = pos;
        for (i = 0; i < n_flt; ++i) nova_biquad_flush(&st->filters[i]);
    }
}

typedef struct NovaGuitarWidenerState {
    double delay_z;
    double z_a[4][2];
    double z_b[4][2];
} NovaGuitarWidenerState;

NOVA_API void NOVA_CALL nova_guitar_widener_reset(NovaGuitarWidenerState* st) {
    if (!st) {
        return;
    }
    memset(st, 0, sizeof(NovaGuitarWidenerState));
}

NOVA_API void NOVA_CALL nova_guitar_widener_process(
    NovaGuitarWidenerState* st,
    const double* in_mono,
    double* out_l,
    double* out_r,
    int32_t frames,
    double stereo_width
) {
    static const double CA[4] = {
        0.6923878 * 0.6923878,
        0.9360654322959 * 0.9360654322959,
        0.9882295226860 * 0.9882295226860,
        0.9987488452737 * 0.9987488452737
    };
    static const double CB[4] = {
        0.4021921162426 * 0.4021921162426,
        0.8561710882420 * 0.8561710882420,
        0.9722909545651 * 0.9722909545651,
        0.9952884791278 * 0.9952884791278
    };
    double w, k, norm;
    int32_t i, s;

    if (!st || !in_mono || !out_l || !out_r || frames <= 0) {
        return;
    }

    w = isfinite(stereo_width) ? nova_clamp_d(stereo_width, 0.0, 1.0) : 0.0;
    if (w <= 0.001) {
        for (i = 0; i < frames; ++i) {
            double x = nova_sanitize_d(in_mono[i]);
            out_l[i] = x;
            out_r[i] = x;
        }
        return;
    }

    k = w * 0.78;
    norm = 1.0 / sqrt(1.0 + k * k);

    for (i = 0; i < frames; ++i) {
        double x = nova_sanitize_d(in_mono[i]);
        double sig_i = st->delay_z;
        double sig_q = x;
        st->delay_z = x;

        for (s = 0; s < 4; ++s) {
            double c = CA[s];
            double y = -c * sig_i + st->z_a[s][0];
            st->z_a[s][0] = st->z_a[s][1];
            st->z_a[s][1] = sig_i + c * y;
            sig_i = y;
        }
        for (s = 0; s < 4; ++s) {
            double c = CB[s];
            double y = -c * sig_q + st->z_b[s][0];
            st->z_b[s][0] = st->z_b[s][1];
            st->z_b[s][1] = sig_q + c * y;
            sig_q = y;
        }

        out_l[i] = (sig_i + k * sig_q) * norm;
        out_r[i] = (sig_i - k * sig_q) * norm;
    }

    st->delay_z = nova_flush_denormal_d(st->delay_z);
    for (s = 0; s < 4; ++s) {
        st->z_a[s][0] = nova_flush_denormal_d(st->z_a[s][0]);
        st->z_a[s][1] = nova_flush_denormal_d(st->z_a[s][1]);
        st->z_b[s][0] = nova_flush_denormal_d(st->z_b[s][0]);
        st->z_b[s][1] = nova_flush_denormal_d(st->z_b[s][1]);
    }
}

/* --------------------------------------------------------------------------
 * 7. Synth & Shared Reverb Kernels (Osc, ADSR, SVF, Delay, Reverb, Layer)
 * -------------------------------------------------------------------------- */

static uint64_t nova_xorshift64(uint64_t* state) {
    uint64_t x = *state;
    if (x == 0) x = 0x9E3779B97F4A7C15ULL;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    *state = x;
    return x * 0x2545F4914F6CDD1DULL;
}

static double nova_polyblep_saw_sample(double phase, double dt) {
    double s = 2.0 * phase - 1.0;
    if (phase < dt) {
        double t1 = phase / dt;
        s -= (2.0 * t1 - t1 * t1 - 1.0);
    } else if (phase > 1.0 - dt) {
        double t2 = (phase - 1.0) / dt;
        s -= (t2 * t2 + 2.0 * t2 + 1.0);
    }
    return s;
}

static void nova_synth_osc_sample(
    int32_t wave_id,
    double freq_clamped,
    double dt,
    double sr,
    double t,
    double phase_offset,
    double pw,
    double fm_ratio_c,
    double fm_depth_c,
    const double* f_voices,
    const double* dt_voices,
    const double* gains_l,
    const double* gains_r,
    double* out_l,
    double* out_r
) {
    double p = t * freq_clamped + phase_offset;
    double phase = p - floor(p);
    double m = 0.0;

    if (wave_id == 0) {
        m = sin(2.0 * M_PI * phase);
    } else if (wave_id == 1) {
        m = 2.0 * fabs(2.0 * (phase - floor(phase + 0.5))) - 1.0;
    } else if (wave_id == 2) {
        m = nova_polyblep_saw_sample(phase, dt);
    } else if (wave_id == 3) {
        double s = (phase < pw) ? 1.0 : -1.0;
        double p_pw_raw = phase - pw;
        double p_pw = p_pw_raw - floor(p_pw_raw);
        if (phase < dt) {
            double t1 = phase / dt;
            s += (2.0 * t1 - t1 * t1 - 1.0);
        }
        if (phase > 1.0 - dt) {
            double t2 = (phase - 1.0) / dt;
            s += (t2 * t2 + 2.0 * t2 + 1.0);
        }
        if (p_pw < dt) {
            double tp1 = p_pw / dt;
            s -= (2.0 * tp1 - tp1 * tp1 - 1.0);
        }
        if (p_pw > 1.0 - dt) {
            double tp2 = (p_pw - 1.0) / dt;
            s -= (tp2 * tp2 + 2.0 * tp2 + 1.0);
        }
        m = s;
    } else if (wave_id == 5) {
        double m_freq = freq_clamped * fm_ratio_c;
        double mod = sin(2.0 * M_PI * m_freq * t);
        double car = 2.0 * M_PI * freq_clamped * t + phase_offset * 2.0 * M_PI + fm_depth_c * mod;
        m = sin(car);
    } else if (wave_id == 6) {
        static const double SEEDS_7[7] = {
            0.0, 0.142857, 0.285714, 0.428571, 0.571428, 0.714285, 0.857142
        };
        double norm7 = 1.0 / sqrt(7.0);
        double l_sum = 0.0, r_sum = 0.0;
        int v;
        (void)sr;
        for (v = 0; v < 7; ++v) {
            double pv_raw = t * f_voices[v] + SEEDS_7[v] + phase_offset;
            double pv = pv_raw - floor(pv_raw);
            double sv = nova_polyblep_saw_sample(pv, dt_voices[v]);
            l_sum += sv * gains_l[v];
            r_sum += sv * gains_r[v];
        }
        *out_l = l_sum * norm7;
        *out_r = r_sum * norm7;
        return;
    } else {
        m = sin(2.0 * M_PI * phase);
    }
    *out_l = m;
    *out_r = m;
}

NOVA_API void NOVA_CALL nova_synth_osc_process(
    int32_t wave_id,
    double freq,
    int32_t frames,
    int32_t sample_rate,
    double phase_offset,
    double pulse_width,
    double fm_ratio,
    double fm_depth,
    double unison_detune,
    double unison_spread,
    double time_offset,
    const double* t_vec_opt,
    uint64_t* rng_state,
    float* out_stereo
) {
    static const double DETUNE_7[7] = {-0.11, -0.06, -0.02, 0.0, 0.02, 0.06, 0.11};
    double sr, freq_c, dt, pw, fm_r, fm_d;
    double f_voices[7], dt_voices[7], gains_l[7], gains_r[7];
    int32_t i, v;

    if (!out_stereo || frames <= 0 || sample_rate <= 0) {
        return;
    }

    sr = (double)sample_rate;
    freq_c = nova_clamp_d(freq, 10.0, sr * 0.49);
    dt = freq_c / sr;
    pw = nova_clamp_d(pulse_width, 0.05, 0.95);
    fm_r = nova_clamp_d(fm_ratio, 0.25, 16.0);
    fm_d = nova_clamp_d(fm_depth, 0.0, 10.0);

    if (wave_id == 4) {
        uint64_t local_rng = (rng_state && *rng_state != 0) ? *rng_state : 0x123456789ABCDEF0ULL;
        double b0 = 0.0;
        for (i = 0; i < frames; ++i) {
            uint64_t r = nova_xorshift64(&local_rng);
            double u = (double)(r >> 11) * (1.0 / 9007199254740992.0);
            double white = u * 2.0 - 1.0;
            double pink;
            b0 = 0.95 * b0 + 0.05 * white;
            pink = (white + b0 * 2.0) * 0.5;
            out_stereo[2 * i] = (float)pink;
            out_stereo[2 * i + 1] = (float)pink;
        }
        if (rng_state) *rng_state = local_rng;
        return;
    }

    if (wave_id == 6) {
        double u_det = nova_clamp_d(unison_detune, 0.01, 1.0);
        for (v = 0; v < 7; ++v) {
            double pan = -unison_spread + (2.0 * unison_spread) * ((double)v / 6.0);
            double gl = 0.5 * (1.0 - pan);
            double gr = 0.5 * (1.0 + pan);
            f_voices[v] = nova_clamp_d(freq_c * pow(2.0, (DETUNE_7[v] * u_det) / 12.0), 10.0, sr * 0.49);
            dt_voices[v] = f_voices[v] / sr;
            gains_l[v] = sqrt((gl > 0.0) ? gl : 0.0);
            gains_r[v] = sqrt((gr > 0.0) ? gr : 0.0);
        }
    }

    for (i = 0; i < frames; ++i) {
        double t = t_vec_opt ? t_vec_opt[i] : (((double)i / sr) + time_offset);
        double ol = 0.0, or_ch = 0.0;
        nova_synth_osc_sample(
            wave_id, freq_c, dt, sr, t, phase_offset, pw, fm_r, fm_d,
            f_voices, dt_voices, gains_l, gains_r, &ol, &or_ch
        );
        out_stereo[2 * i] = nova_sanitize_f32_out(ol);
        out_stereo[2 * i + 1] = nova_sanitize_f32_out(or_ch);
    }
}

static double nova_adsr_sample(
    double t,
    double note_off_time,
    double a,
    double d,
    double s,
    double r,
    double sus_val
) {
    if (t < 0.0) {
        return 0.0;
    }
    if (t < note_off_time) {
        if (t < a) {
            return nova_clamp_d(t / a, 0.0, 1.0);
        }
        if (t < a + d) {
            return nova_clamp_d(1.0 - ((t - a) / d) * (1.0 - s), 0.0, 1.0);
        }
        return s;
    } else {
        double t_rel = t - note_off_time;
        double norm_rel = nova_clamp_d(1.0 - (t_rel / r), 0.0, 1.0);
        return nova_clamp_d(sus_val * norm_rel * norm_rel, 0.0, 1.0);
    }
}

NOVA_API void NOVA_CALL nova_synth_adsr_process(
    double t_start,
    double t_end,
    int32_t frames,
    double note_off_time,
    double attack,
    double decay,
    double sustain,
    double release,
    float* out_env
) {
    double a, d, s, r, sus_val, dt;
    int32_t i;

    if (!out_env || frames <= 0) {
        return;
    }

    a = (attack > 0.001) ? attack : 0.001;
    d = (decay > 0.001) ? decay : 0.001;
    s = nova_clamp_d(sustain, 0.0, 1.0);
    r = (release > 0.001) ? release : 0.001;

    if (t_end <= 0.0 || t_start >= (note_off_time + r)) {
        memset(out_env, 0, (size_t)frames * sizeof(float));
        return;
    }
    if (t_start >= (a + d) && t_end <= note_off_time) {
        float sf = (float)s;
        for (i = 0; i < frames; ++i) {
            out_env[i] = sf;
        }
        return;
    }

    if (note_off_time < a) {
        sus_val = (note_off_time / a > 0.0) ? (note_off_time / a) : 0.0;
    } else if (note_off_time < (a + d)) {
        sus_val = 1.0 - ((note_off_time - a) / d) * (1.0 - s);
    } else {
        sus_val = s;
    }

    dt = (t_end - t_start) / (double)frames;
    for (i = 0; i < frames; ++i) {
        double t = t_start + (double)i * dt;
        out_env[i] = (float)nova_adsr_sample(t, note_off_time, a, d, s, r, sus_val);
    }
}

typedef struct NovaSVFState {
    double prev_cutoff;
    double prev_q;
    int32_t prev_type;
    int32_t prev_sr;
    int32_t initialized;
    int32_t has_prev;
    double b0, b1, b2, a1, a2;
    double zi_l[2];
    double zi_r[2];
    double prev_x_l[2];
    double prev_x_r[2];
    double prev_y_l[2];
    double prev_y_r[2];
} NovaSVFState;

NOVA_API void NOVA_CALL nova_svf_reset(NovaSVFState* st) {
    if (!st) {
        return;
    }
    memset(st, 0, sizeof(NovaSVFState));
    st->prev_cutoff = -1.0;
    st->prev_q = -1.0;
    st->prev_type = -1;
}

static void nova_svf_prepare(
    NovaSVFState* st,
    int32_t sr,
    int32_t filter_type,
    double c_eff,
    double q
) {
    int need_coeffs = (!st->initialized ||
                       fabs(c_eff - st->prev_cutoff) > 0.05 ||
                       fabs(q - st->prev_q) > 0.01 ||
                       filter_type != st->prev_type ||
                       sr != st->prev_sr);

    if (need_coeffs) {
        double c_hz = nova_clamp_d(c_eff, 20.0, (double)sr * 0.48);
        double w0 = 2.0 * M_PI * c_hz / (double)sr;
        double cw = cos(w0);
        double sw = sin(w0);
        double alpha = sw / (2.0 * q);
        double a0 = 1.0 + alpha;
        double nb0, nb1, nb2;
        if (filter_type == 1) {
            nb0 = (1.0 + cw) * 0.5;
            nb1 = -(1.0 + cw);
            nb2 = (1.0 + cw) * 0.5;
        } else if (filter_type == 2) {
            nb0 = alpha;
            nb1 = 0.0;
            nb2 = -alpha;
        } else if (filter_type == 3) {
            nb0 = 1.0;
            nb1 = -2.0 * cw;
            nb2 = 1.0;
        } else {
            nb0 = (1.0 - cw) * 0.5;
            nb1 = 1.0 - cw;
            nb2 = (1.0 - cw) * 0.5;
        }
        st->b0 = nb0 / a0;
        st->b1 = nb1 / a0;
        st->b2 = nb2 / a0;
        st->a1 = (-2.0 * cw) / a0;
        st->a2 = (1.0 - alpha) / a0;
    }

    if (!st->initialized) {
        st->zi_l[0] = 0.0; st->zi_l[1] = 0.0;
        st->zi_r[0] = 0.0; st->zi_r[1] = 0.0;
    } else if (st->has_prev &&
               (fabs(c_eff - st->prev_cutoff) > 100.0 ||
                fabs(q - st->prev_q) > 0.5 ||
                filter_type != st->prev_type)) {
        st->zi_l[0] = st->b1 * st->prev_x_l[1] + st->b2 * st->prev_x_l[0]
                    - st->a1 * st->prev_y_l[1] - st->a2 * st->prev_y_l[0];
        st->zi_l[1] = st->b2 * st->prev_x_l[1] - st->a2 * st->prev_y_l[1];
        st->zi_r[0] = st->b1 * st->prev_x_r[1] + st->b2 * st->prev_x_r[0]
                    - st->a1 * st->prev_y_r[1] - st->a2 * st->prev_y_r[0];
        st->zi_r[1] = st->b2 * st->prev_x_r[1] - st->a2 * st->prev_y_r[1];
    }
}

NOVA_API void NOVA_CALL nova_svf_process(
    NovaSVFState* st,
    const float* in_stereo,
    float* out_stereo,
    int32_t frames,
    int32_t sample_rate,
    int32_t filter_type,
    double cutoff,
    double resonance,
    double drive,
    const float* mod_cutoff
) {
    double drive_c, drive_gain, q, c_eff;
    double b0, b1, b2, a1, a2, zl0, zl1, zr0, zr1;
    double px_l0 = 0.0, px_l1 = 0.0, px_r0 = 0.0, px_r1 = 0.0;
    double py_l0 = 0.0, py_l1 = 0.0, py_r0 = 0.0, py_r1 = 0.0;
    int32_t sr, i;
    int use_drive_in, use_drive_tanh;

    if (!st || !in_stereo || !out_stereo || frames <= 0) {
        return;
    }

    sr = (sample_rate > 0) ? sample_rate : 44100;
    drive_c = isfinite(drive) ? nova_clamp_d(drive, 0.0, 3.0) : 0.0;
    drive_gain = 1.0 + drive_c * 0.8;
    use_drive_in = (drive > 0.01);
    use_drive_tanh = (drive > 0.05);
    q = isfinite(resonance) ? nova_clamp_d(resonance, 0.2, 10.0) : 1.0;

    if (mod_cutoff) {
        double sum = 0.0;
        double m_val;
        for (i = 0; i < frames; ++i) {
            sum += nova_sanitize_d((double)mod_cutoff[i]);
        }
        m_val = sum / (double)frames;
        c_eff = nova_clamp_d(cutoff * pow(2.0, m_val), 20.0, (double)sr * 0.48);
    } else {
        c_eff = nova_clamp_d(cutoff, 20.0, (double)sr * 0.48);
    }

    if (filter_type == 0 && c_eff >= ((double)sr * 0.44) && drive <= 0.05 && !mod_cutoff) {
        double gain = use_drive_in ? drive_gain : 1.0;
        for (i = 0; i < frames * 2; ++i) {
            out_stereo[i] = nova_sanitize_f32_out(nova_sanitize_d((double)in_stereo[i]) * gain);
        }
        return;
    }

    nova_svf_prepare(st, sr, filter_type, c_eff, q);

    b0 = st->b0; b1 = st->b1; b2 = st->b2; a1 = st->a1; a2 = st->a2;
    zl0 = st->zi_l[0]; zl1 = st->zi_l[1];
    zr0 = st->zi_r[0]; zr1 = st->zi_r[1];

    for (i = 0; i < frames; ++i) {
        double xl = nova_sanitize_d((double)in_stereo[2 * i]);
        double xr = nova_sanitize_d((double)in_stereo[2 * i + 1]);
        double yl, yr;
        if (use_drive_in) {
            xl *= drive_gain;
            xr *= drive_gain;
        }
        yl = b0 * xl + zl0;
        zl0 = b1 * xl - a1 * yl + zl1;
        zl1 = b2 * xl - a2 * yl;

        yr = b0 * xr + zr0;
        zr0 = b1 * xr - a1 * yr + zr1;
        zr1 = b2 * xr - a2 * yr;

        if (i == frames - 2) {
            px_l0 = xl; px_r0 = xr;
            py_l0 = yl; py_r0 = yr;
        } else if (i == frames - 1) {
            px_l1 = xl; px_r1 = xr;
            py_l1 = yl; py_r1 = yr;
        }

        if (use_drive_tanh) {
            yl = tanh(yl / drive_gain) * drive_gain;
            yr = tanh(yr / drive_gain) * drive_gain;
        }

        out_stereo[2 * i] = nova_sanitize_f32_out(yl);
        out_stereo[2 * i + 1] = nova_sanitize_f32_out(yr);
    }

    st->zi_l[0] = nova_flush_denormal_d(zl0);
    st->zi_l[1] = nova_flush_denormal_d(zl1);
    st->zi_r[0] = nova_flush_denormal_d(zr0);
    st->zi_r[1] = nova_flush_denormal_d(zr1);

    if (frames >= 2) {
        st->prev_x_l[0] = px_l0; st->prev_x_l[1] = px_l1;
        st->prev_x_r[0] = px_r0; st->prev_x_r[1] = px_r1;
        st->prev_y_l[0] = py_l0; st->prev_y_l[1] = py_l1;
        st->prev_y_r[0] = py_r0; st->prev_y_r[1] = py_r1;
        st->has_prev = 1;
    }
    st->prev_cutoff = c_eff;
    st->prev_q = q;
    st->prev_type = filter_type;
    st->prev_sr = sr;
    st->initialized = 1;
}

typedef struct NovaDelay {
    int32_t sample_rate;
    int32_t max_delay_samples;
    int32_t write_idx;
    float* buffer_l;
    float* buffer_r;
} NovaDelay;

NOVA_API NovaDelay* NOVA_CALL nova_delay_create(int32_t sample_rate) {
    NovaDelay* ctx;
    int32_t sr = (sample_rate > 0) ? sample_rate : 44100;
    int32_t max_d = (int32_t)((double)sr * 2.0);
    if (max_d < 16) max_d = 16;

    ctx = (NovaDelay*)calloc(1, sizeof(NovaDelay));
    if (!ctx) return NULL;
    ctx->buffer_l = (float*)calloc((size_t)max_d, sizeof(float));
    ctx->buffer_r = (float*)calloc((size_t)max_d, sizeof(float));
    if (!ctx->buffer_l || !ctx->buffer_r) {
        free(ctx->buffer_l);
        free(ctx->buffer_r);
        free(ctx);
        return NULL;
    }
    ctx->sample_rate = sr;
    ctx->max_delay_samples = max_d;
    ctx->write_idx = 0;
    return ctx;
}

NOVA_API void NOVA_CALL nova_delay_destroy(NovaDelay* ctx) {
    if (ctx) {
        free(ctx->buffer_l);
        free(ctx->buffer_r);
        free(ctx);
    }
}

NOVA_API void NOVA_CALL nova_delay_reset(NovaDelay* ctx) {
    if (!ctx) return;
    if (ctx->buffer_l) memset(ctx->buffer_l, 0, (size_t)ctx->max_delay_samples * sizeof(float));
    if (ctx->buffer_r) memset(ctx->buffer_r, 0, (size_t)ctx->max_delay_samples * sizeof(float));
    ctx->write_idx = 0;
}

NOVA_API void NOVA_CALL nova_delay_process(
    NovaDelay* ctx,
    const float* in_stereo,
    float* out_stereo,
    int32_t frames,
    double time_sec,
    double feedback,
    int32_t ping_pong,
    double mix,
    int32_t enabled
) {
    int32_t max_d, delay_samples, w_idx, i;
    double fb, mix_c, dry_gain;

    if (!ctx || !in_stereo || !out_stereo || frames <= 0) {
        return;
    }

    mix_c = isfinite(mix) ? nova_clamp_d(mix, 0.0, 1.0) : 0.0;
    if (!enabled || mix_c <= 0.001) {
        if (out_stereo != in_stereo) {
            for (i = 0; i < frames * 2; ++i) {
                out_stereo[i] = nova_sanitize_f32_out((double)in_stereo[i]);
            }
        }
        return;
    }

    max_d = ctx->max_delay_samples;
    delay_samples = (int32_t)(time_sec * (double)ctx->sample_rate);
    if (delay_samples < 10) delay_samples = 10;
    if (delay_samples > max_d - 1) delay_samples = max_d - 1;

    fb = isfinite(feedback) ? nova_clamp_d(feedback, 0.0, 0.95) : 0.0;
    dry_gain = 1.0 - mix_c * 0.5;
    w_idx = ctx->write_idx;

    for (i = 0; i < frames; ++i) {
        int32_t r_idx = w_idx - delay_samples;
        double in_l = nova_sanitize_d((double)in_stereo[2 * i]);
        double in_r = nova_sanitize_d((double)in_stereo[2 * i + 1]);
        double wet_l, wet_r;
        if (r_idx < 0) r_idx += max_d;
        wet_l = (double)ctx->buffer_l[r_idx];
        wet_r = (double)ctx->buffer_r[r_idx];

        if (ping_pong) {
            ctx->buffer_l[w_idx] = nova_sanitize_f32_out(in_l + wet_r * fb);
            ctx->buffer_r[w_idx] = nova_sanitize_f32_out(in_r + wet_l * fb);
        } else {
            ctx->buffer_l[w_idx] = nova_sanitize_f32_out(in_l + wet_l * fb);
            ctx->buffer_r[w_idx] = nova_sanitize_f32_out(in_r + wet_r * fb);
        }

        w_idx++;
        if (w_idx >= max_d) w_idx = 0;

        out_stereo[2 * i] = nova_sanitize_f32_out(in_l * dry_gain + wet_l * mix_c);
        out_stereo[2 * i + 1] = nova_sanitize_f32_out(in_r * dry_gain + wet_r * mix_c);
    }

    ctx->write_idx = w_idx;
}

typedef struct NovaReverb {
    int32_t sample_rate;
    int32_t comb_lens_l[4], comb_lens_r[4];
    int32_t comb_idx_l[4], comb_idx_r[4];
    double comb_store_l[4], comb_store_r[4];
    float* comb_buf_l[4];
    float* comb_buf_r[4];
    int32_t ap_lens_l[2], ap_lens_r[2];
    int32_t ap_idx_l[2], ap_idx_r[2];
    float* ap_buf_l[2];
    float* ap_buf_r[2];
} NovaReverb;

NOVA_API void NOVA_CALL nova_reverb_destroy(NovaReverb* ctx) {
    int i;
    if (!ctx) return;
    for (i = 0; i < 4; ++i) {
        free(ctx->comb_buf_l[i]);
        free(ctx->comb_buf_r[i]);
    }
    for (i = 0; i < 2; ++i) {
        free(ctx->ap_buf_l[i]);
        free(ctx->ap_buf_r[i]);
    }
    free(ctx);
}

NOVA_API NovaReverb* NOVA_CALL nova_reverb_create(int32_t sample_rate) {
    static const int32_t COMB_BASE[4] = {1116, 1277, 1422, 1617};
    static const int32_t AP_BASE[2] = {556, 341};
    NovaReverb* ctx;
    double scale;
    int i;

    ctx = (NovaReverb*)calloc(1, sizeof(NovaReverb));
    if (!ctx) return NULL;
    ctx->sample_rate = sample_rate;
    scale = ((double)sample_rate / 44100.0 > 0.1) ? ((double)sample_rate / 44100.0) : 0.1;

    for (i = 0; i < 4; ++i) {
        int32_t cl = (int32_t)((double)COMB_BASE[i] * scale);
        int32_t cr = (int32_t)((double)(COMB_BASE[i] + 23) * scale);
        if (cl < 2) cl = 2;
        if (cr < 2) cr = 2;
        ctx->comb_lens_l[i] = cl;
        ctx->comb_lens_r[i] = cr;
        ctx->comb_buf_l[i] = (float*)calloc((size_t)cl, sizeof(float));
        ctx->comb_buf_r[i] = (float*)calloc((size_t)cr, sizeof(float));
        if (!ctx->comb_buf_l[i] || !ctx->comb_buf_r[i]) {
            nova_reverb_destroy(ctx);
            return NULL;
        }
    }

    for (i = 0; i < 2; ++i) {
        int32_t al = (int32_t)((double)AP_BASE[i] * scale);
        int32_t ar = (int32_t)((double)(AP_BASE[i] + 23) * scale);
        if (al < 2) al = 2;
        if (ar < 2) ar = 2;
        ctx->ap_lens_l[i] = al;
        ctx->ap_lens_r[i] = ar;
        ctx->ap_buf_l[i] = (float*)calloc((size_t)al, sizeof(float));
        ctx->ap_buf_r[i] = (float*)calloc((size_t)ar, sizeof(float));
        if (!ctx->ap_buf_l[i] || !ctx->ap_buf_r[i]) {
            nova_reverb_destroy(ctx);
            return NULL;
        }
    }

    return ctx;
}

NOVA_API void NOVA_CALL nova_reverb_reset(NovaReverb* ctx) {
    int i;
    if (!ctx) return;
    for (i = 0; i < 4; ++i) {
        if (ctx->comb_buf_l[i]) memset(ctx->comb_buf_l[i], 0, (size_t)ctx->comb_lens_l[i] * sizeof(float));
        if (ctx->comb_buf_r[i]) memset(ctx->comb_buf_r[i], 0, (size_t)ctx->comb_lens_r[i] * sizeof(float));
        ctx->comb_idx_l[i] = 0;
        ctx->comb_idx_r[i] = 0;
        ctx->comb_store_l[i] = 0.0;
        ctx->comb_store_r[i] = 0.0;
    }
    for (i = 0; i < 2; ++i) {
        if (ctx->ap_buf_l[i]) memset(ctx->ap_buf_l[i], 0, (size_t)ctx->ap_lens_l[i] * sizeof(float));
        if (ctx->ap_buf_r[i]) memset(ctx->ap_buf_r[i], 0, (size_t)ctx->ap_lens_r[i] * sizeof(float));
        ctx->ap_idx_l[i] = 0;
        ctx->ap_idx_r[i] = 0;
    }
}

NOVA_API void NOVA_CALL nova_reverb_process(
    NovaReverb* ctx,
    const float* in_stereo,
    float* out_stereo,
    int32_t frames,
    double room_size,
    double damping,
    double mix,
    int32_t enabled
) {
    double mix_c, fb, damp, gain_fb, dry_gain, wet_gain;
    int32_t i, k;

    if (!ctx || !in_stereo || !out_stereo || frames <= 0) {
        return;
    }

    mix_c = isfinite(mix) ? nova_clamp_d(mix, 0.0, 1.0) : 0.0;
    if (!enabled || mix_c <= 0.001) {
        if (out_stereo != in_stereo) {
            for (i = 0; i < frames * 2; ++i) {
                out_stereo[i] = nova_sanitize_f32_out((double)in_stereo[i]);
            }
        }
        return;
    }

    fb = 0.72 + (isfinite(room_size) ? room_size : 0.5) * 0.25;
    damp = (isfinite(damping) ? damping : 0.35) * 0.45;
    gain_fb = fb * (1.0 - damp);
    dry_gain = 1.0 - (mix_c * 0.45);
    wet_gain = mix_c * 0.75;

    for (i = 0; i < frames; ++i) {
        double in_l = nova_sanitize_d((double)in_stereo[2 * i]);
        double in_r = nova_sanitize_d((double)in_stereo[2 * i + 1]);
        double mono_in = (in_l + in_r) * 0.08;
        double wet_l = 0.0, wet_r = 0.0;

        /* 4 L + 4 R damped comb filters */
        for (k = 0; k < 4; ++k) {
            int32_t il = ctx->comb_idx_l[k];
            int32_t ir = ctx->comb_idx_r[k];
            double dl = (double)ctx->comb_buf_l[k][il];
            double dr = (double)ctx->comb_buf_r[k][ir];
            double store_l = dl + ctx->comb_store_l[k];
            double store_r = dr + ctx->comb_store_r[k];

            ctx->comb_store_l[k] = damp * store_l;
            ctx->comb_store_r[k] = damp * store_r;

            ctx->comb_buf_l[k][il] = nova_sanitize_f32_out(mono_in + store_l * gain_fb);
            ctx->comb_buf_r[k][ir] = nova_sanitize_f32_out(mono_in + store_r * gain_fb);

            il++; if (il >= ctx->comb_lens_l[k]) il = 0;
            ir++; if (ir >= ctx->comb_lens_r[k]) ir = 0;
            ctx->comb_idx_l[k] = il;
            ctx->comb_idx_r[k] = ir;

            wet_l += dl;
            wet_r += dr;
        }

        /* 2 L + 2 R allpass diffusers (exact HAS_SCIPY_SIG=True math) */
        for (k = 0; k < 2; ++k) {
            int32_t al = ctx->ap_idx_l[k];
            int32_t ar = ctx->ap_idx_r[k];
            double bo_l = (double)ctx->ap_buf_l[k][al];
            double bo_r = (double)ctx->ap_buf_r[k][ar];

            ctx->ap_buf_l[k][al] = nova_sanitize_f32_out(wet_l + bo_l * 0.5);
            ctx->ap_buf_r[k][ar] = nova_sanitize_f32_out(wet_r + bo_r * 0.5);
            wet_l = bo_l - wet_l * 0.5;
            wet_r = bo_r - wet_r * 0.5;

            al++; if (al >= ctx->ap_lens_l[k]) al = 0;
            ar++; if (ar >= ctx->ap_lens_r[k]) ar = 0;
            ctx->ap_idx_l[k] = al;
            ctx->ap_idx_r[k] = ar;
        }

        out_stereo[2 * i] = nova_sanitize_f32_out(in_l * dry_gain + wet_l * wet_gain);
        out_stereo[2 * i + 1] = nova_sanitize_f32_out(in_r * dry_gain + wet_r * wet_gain);
    }

    for (k = 0; k < 4; ++k) {
        ctx->comb_store_l[k] = nova_flush_denormal_d(ctx->comb_store_l[k]);
        ctx->comb_store_r[k] = nova_flush_denormal_d(ctx->comb_store_r[k]);
    }
}

NOVA_API int32_t NOVA_CALL nova_synth_render_layer(
    NovaSVFState* svf,
    float* bus_stereo,
    int32_t frames,
    int32_t sample_rate,
    double t_start_rel,
    double t_end_rel,
    double note_off_rel,
    double attack,
    double decay,
    double sustain,
    double release,
    double volume,
    double vel,
    double pan,
    double freq,
    int32_t wave_id,
    double pulse_width,
    double fm_ratio,
    double fm_depth,
    double unison_detune,
    double unison_spread,
    double filter_env_amount,
    double f_attack,
    double f_decay,
    double f_sustain,
    double f_release,
    int32_t lfo_enabled,
    int32_t lfo_wave_id,
    double lfo_rate,
    double lfo_depth,
    int32_t lfo_target,
    double slice_start_sec,
    double slice_end_sec,
    int32_t filter_type,
    double cutoff,
    double resonance,
    double drive
) {
    static const double DETUNE_7[7] = {-0.11, -0.06, -0.02, 0.0, 0.02, 0.06, 0.11};
    double a, d, s, r, sus_val, dt_env;
    double fa, fd, fs, fr, fsus_val;
    double max_env = 0.0;
    double sr_d;
    int lfo_active, has_fenv, has_mod_cutoff;
    double lfo_dt_sec = 0.0;
    double mod_cutoff_sum = 0.0, lfo_pitch_sum = 0.0;
    double eff_freq, freq_c, osc_dt, pw, fm_r, fm_d;
    double f_voices[7], dt_voices[7], gains_l[7], gains_r[7];
    double drive_c, drive_gain, q, c_eff;
    double b0, b1, b2, a1, a2, zl0, zl1, zr0, zr1;
    double px_l0 = 0.0, px_l1 = 0.0, px_r0 = 0.0, px_r1 = 0.0;
    double py_l0 = 0.0, py_l1 = 0.0, py_r0 = 0.0, py_r1 = 0.0;
    double gain_l, gain_r, vol_vel;
    int svf_bypass, use_drive_in, use_drive_tanh;
    int32_t i, v;

    if (!svf || !bus_stereo || frames <= 0 || sample_rate <= 0) {
        return 0;
    }

    a = (attack > 0.001) ? attack : 0.001;
    d = (decay > 0.001) ? decay : 0.001;
    s = nova_clamp_d(sustain, 0.0, 1.0);
    r = (release > 0.001) ? release : 0.001;

    if (t_end_rel <= 0.0 || t_start_rel >= (note_off_rel + r)) {
        return 0;
    }

    if (note_off_rel < a) {
        sus_val = (note_off_rel / a > 0.0) ? (note_off_rel / a) : 0.0;
    } else if (note_off_rel < (a + d)) {
        sus_val = 1.0 - ((note_off_rel - a) / d) * (1.0 - s);
    } else {
        sus_val = s;
    }
    dt_env = (t_end_rel - t_start_rel) / (double)frames;

    if (t_start_rel >= (a + d) && t_end_rel <= note_off_rel) {
        max_env = s;
    } else {
        for (i = 0; i < frames; ++i) {
            double env_v = nova_adsr_sample(t_start_rel + (double)i * dt_env, note_off_rel, a, d, s, r, sus_val);
            if (env_v > max_env) max_env = env_v;
        }
    }

    vol_vel = volume * vel;
    if (max_env <= 0.003 || (max_env * vol_vel) <= 0.002) {
        return 0;
    }

    sr_d = (double)sample_rate;
    has_fenv = (fabs(filter_env_amount) > 0.01);
    lfo_active = (lfo_enabled && lfo_depth > 0.01);
    has_mod_cutoff = (has_fenv || (lfo_active && lfo_target == 0));

    fa = (f_attack > 0.001) ? f_attack : 0.001;
    fd = (f_decay > 0.001) ? f_decay : 0.001;
    fs = nova_clamp_d(f_sustain, 0.0, 1.0);
    fr = (f_release > 0.001) ? f_release : 0.001;
    if (note_off_rel < fa) {
        fsus_val = (note_off_rel / fa > 0.0) ? (note_off_rel / fa) : 0.0;
    } else if (note_off_rel < (fa + fd)) {
        fsus_val = 1.0 - ((note_off_rel - fa) / fd) * (1.0 - fs);
    } else {
        fsus_val = fs;
    }

    if (lfo_active) {
        lfo_dt_sec = (slice_end_sec - slice_start_sec) / (double)frames;
    }

    /* Pass 1: compute block-mean mod_cutoff and lfo_pitch if active */
    if (has_mod_cutoff || (lfo_active && lfo_target == 1)) {
        for (i = 0; i < frames; ++i) {
            if (has_fenv) {
                double fe = nova_adsr_sample(t_start_rel + (double)i * dt_env, note_off_rel, fa, fd, fs, fr, fsus_val);
                mod_cutoff_sum += fe * filter_env_amount * 3.5;
            }
            if (lfo_active && (lfo_target == 0 || lfo_target == 1)) {
                double tv = slice_start_sec + (double)i * lfo_dt_sec;
                double lp_raw = tv * lfo_rate;
                double lp = lp_raw - floor(lp_raw);
                double lfo_v = 0.0;
                if (lfo_wave_id == 0) lfo_v = sin(2.0 * M_PI * lp);
                else if (lfo_wave_id == 1) lfo_v = 2.0 * fabs(2.0 * (lp - floor(lp + 0.5))) - 1.0;
                else if (lfo_wave_id == 2) lfo_v = 2.0 * lp - 1.0;
                else if (lfo_wave_id == 3) lfo_v = (lp < 0.5) ? 1.0 : -1.0;
                if (lfo_target == 0) mod_cutoff_sum += lfo_v * lfo_depth * 2.0;
                else lfo_pitch_sum += lfo_v * lfo_depth;
            }
        }
    }

    eff_freq = freq;
    if (lfo_active && lfo_target == 1) {
        eff_freq *= pow(2.0, (lfo_pitch_sum / (double)frames) * 0.25);
    }

    freq_c = nova_clamp_d(eff_freq, 10.0, sr_d * 0.49);
    osc_dt = freq_c / sr_d;
    pw = nova_clamp_d(pulse_width, 0.05, 0.95);
    fm_r = nova_clamp_d(fm_ratio, 0.25, 16.0);
    fm_d = nova_clamp_d(fm_depth, 0.0, 10.0);

    if (wave_id == 6) {
        double u_det = nova_clamp_d(unison_detune, 0.01, 1.0);
        for (v = 0; v < 7; ++v) {
            double upan = -unison_spread + (2.0 * unison_spread) * ((double)v / 6.0);
            double ugl = 0.5 * (1.0 - upan);
            double ugr = 0.5 * (1.0 + upan);
            f_voices[v] = nova_clamp_d(freq_c * pow(2.0, (DETUNE_7[v] * u_det) / 12.0), 10.0, sr_d * 0.49);
            dt_voices[v] = f_voices[v] / sr_d;
            gains_l[v] = sqrt((ugl > 0.0) ? ugl : 0.0);
            gains_r[v] = sqrt((ugr > 0.0) ? ugr : 0.0);
        }
    }

    drive_c = isfinite(drive) ? nova_clamp_d(drive, 0.0, 3.0) : 0.0;
    drive_gain = 1.0 + drive_c * 0.8;
    use_drive_in = (drive > 0.01);
    use_drive_tanh = (drive > 0.05);
    q = isfinite(resonance) ? nova_clamp_d(resonance, 0.2, 10.0) : 1.0;

    if (has_mod_cutoff) {
        double m_val = mod_cutoff_sum / (double)frames;
        c_eff = nova_clamp_d(cutoff * pow(2.0, m_val), 20.0, sr_d * 0.48);
    } else {
        c_eff = nova_clamp_d(cutoff, 20.0, sr_d * 0.48);
    }

    svf_bypass = (filter_type == 0 && c_eff >= (sr_d * 0.44) && drive <= 0.05 && !has_mod_cutoff);
    if (!svf_bypass) {
        nova_svf_prepare(svf, sample_rate, filter_type, c_eff, q);
        b0 = svf->b0; b1 = svf->b1; b2 = svf->b2; a1 = svf->a1; a2 = svf->a2;
        zl0 = svf->zi_l[0]; zl1 = svf->zi_l[1];
        zr0 = svf->zi_r[0]; zr1 = svf->zi_r[1];
    } else {
        b0 = 1.0; b1 = 0.0; b2 = 0.0; a1 = 0.0; a2 = 0.0;
        zl0 = 0.0; zl1 = 0.0; zr0 = 0.0; zr1 = 0.0;
    }

    {
        double gl = 0.5 * (1.0 - pan);
        double gr = 0.5 * (1.0 + pan);
        gain_l = sqrt((gl > 0.0) ? gl : 0.0) * vol_vel;
        gain_r = sqrt((gr > 0.0) ? gr : 0.0) * vol_vel;
    }

    for (i = 0; i < frames; ++i) {
        double t_osc = ((double)i / sr_d) + t_start_rel;
        double ol = 0.0, or_ch = 0.0;
        double env_v = nova_adsr_sample(t_start_rel + (double)i * dt_env, note_off_rel, a, d, s, r, sus_val);
        double fl, fr_ch;

        if (lfo_active && lfo_target == 2) {
            double tv = slice_start_sec + (double)i * lfo_dt_sec;
            double lp_raw = tv * lfo_rate;
            double lp = lp_raw - floor(lp_raw);
            double lfo_v = 0.0;
            if (lfo_wave_id == 0) lfo_v = sin(2.0 * M_PI * lp);
            else if (lfo_wave_id == 1) lfo_v = 2.0 * fabs(2.0 * (lp - floor(lp + 0.5))) - 1.0;
            else if (lfo_wave_id == 2) lfo_v = 2.0 * lp - 1.0;
            else if (lfo_wave_id == 3) lfo_v = (lp < 0.5) ? 1.0 : -1.0;
            env_v *= nova_clamp_d(1.0 + lfo_v * lfo_depth * 0.5, 0.0, 1.5);
        }

        nova_synth_osc_sample(
            wave_id, freq_c, osc_dt, sr_d, t_osc, 0.0, pw, fm_r, fm_d,
            f_voices, dt_voices, gains_l, gains_r, &ol, &or_ch
        );

        if (svf_bypass) {
            fl = use_drive_in ? (ol * drive_gain) : ol;
            fr_ch = use_drive_in ? (or_ch * drive_gain) : or_ch;
        } else {
            double xl = use_drive_in ? (ol * drive_gain) : ol;
            double xr = use_drive_in ? (or_ch * drive_gain) : or_ch;

            fl = b0 * xl + zl0;
            zl0 = b1 * xl - a1 * fl + zl1;
            zl1 = b2 * xl - a2 * fl;

            fr_ch = b0 * xr + zr0;
            zr0 = b1 * xr - a1 * fr_ch + zr1;
            zr1 = b2 * xr - a2 * fr_ch;

            if (i == frames - 2) {
                px_l0 = xl; px_r0 = xr;
                py_l0 = fl; py_r0 = fr_ch;
            } else if (i == frames - 1) {
                px_l1 = xl; px_r1 = xr;
                py_l1 = fl; py_r1 = fr_ch;
            }

            if (use_drive_tanh) {
                fl = tanh(fl / drive_gain) * drive_gain;
                fr_ch = tanh(fr_ch / drive_gain) * drive_gain;
            }
        }

        bus_stereo[2 * i] += nova_sanitize_f32_out(fl * env_v * gain_l);
        bus_stereo[2 * i + 1] += nova_sanitize_f32_out(fr_ch * env_v * gain_r);
    }

    if (!svf_bypass) {
        svf->zi_l[0] = nova_flush_denormal_d(zl0);
        svf->zi_l[1] = nova_flush_denormal_d(zl1);
        svf->zi_r[0] = nova_flush_denormal_d(zr0);
        svf->zi_r[1] = nova_flush_denormal_d(zr1);

        if (frames >= 2) {
            svf->prev_x_l[0] = px_l0; svf->prev_x_l[1] = px_l1;
            svf->prev_x_r[0] = px_r0; svf->prev_x_r[1] = px_r1;
            svf->prev_y_l[0] = py_l0; svf->prev_y_l[1] = py_l1;
            svf->prev_y_r[0] = py_r0; svf->prev_y_r[1] = py_r1;
            svf->has_prev = 1;
        }
        svf->prev_cutoff = c_eff;
        svf->prev_q = q;
        svf->prev_type = filter_type;
        svf->prev_sr = sample_rate;
        svf->initialized = 1;
    }

    return 1;
}

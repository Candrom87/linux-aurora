/* SPDX-License-Identifier: MIT */
/* Original implementation of the source-order contract documented in
 * linux-g15c-{power,cs}-formula-2026-11-03.md. No Apple code or data included.
 */
#include "g15c_23j220_power.h"
#include <fenv.h>
#include <float.h>
#include <math.h>
#include <stddef.h>
#include <string.h>

#ifdef __FAST_MATH__
#error "G15C native contract requires IEEE operations without fast-math"
#endif
_Static_assert(FLT_RADIX == 2 && FLT_MANT_DIG == 24 && DBL_MANT_DIG == 53,
               "G15C native contract requires binary32 and binary64");
_Static_assert(sizeof(float) == 4 && sizeof(double) == 8 && FLT_EVAL_METHOD == 0,
               "G15C native contract requires ordinary-width IEEE evaluation");

static float from_bits(uint32_t bits)
{
    float value;
    memcpy(&value, &bits, sizeof(value));
    return value;
}

static uint32_t to_bits(float value)
{
    uint32_t bits;
    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

/* Volatile results prevent contraction/reassociation across source operations,
 * even if the caller builds this file with an FMA-capable compiler target.
 */
static float mul(float a, float b) { volatile float v = a * b; return v; }
static float add(float a, float b) { volatile float v = a + b; return v; }
static float div32(float a, float b) { volatile float v = a / b; return v; }
static double mul64(double a, double b) { volatile double v = a * b; return v; }

static int scalar_valid(uint32_t mv, float leak, const uint32_t *out)
{
    if (!out || mv > 1500 || !isfinite(leak) || leak < 0)
        return G15C_INVALID;
    return fegetround() == FE_TONEAREST ? G15C_OK : G15C_NUMERIC;
}

static int word(float value, uint32_t *out)
{
    if (!isfinite(value) || value < 0 || (double)value >= 4294967296.0)
        return G15C_NUMERIC;
    *out = (uint32_t)value;
    return G15C_OK;
}

static float leakage(float v, float leak, int cs)
{
    static const uint32_t bands[2][3][3] = {
        {{0x3c2dbbcc, 0x3ae2cad0, 0x40489c1e},
         {0x3bdbd5cd, 0x3b23ab1e, 0x4044e298},
         {0x3b1a573a, 0x3b780f9b, 0x403c46cc}},
        {{0x3c29a667, 0x3adb4c5a, 0x404c5033},
         {0x3bd520eb, 0x3b25c84b, 0x40454aa0},
         {0x3b1843b2, 0x3b7c24f1, 0x40398ba7}},
    };
    uint32_t q = (uint32_t)mul(v, 1000.0f);
    const uint32_t *b = bands[cs][q < 700 ? 0 : q < 900 ? 1 : 2];
    float exponent = add(from_bits(b[2]), mul(from_bits(b[1]), (float)q));
    return mul(mul(leak, from_bits(b[0])), powf(from_bits(0x402df854), exponent));
}

int g15c_23j220_decode(const struct g15c_packed_coefficients *in,
                       struct g15c_coefficients *out)
{
    struct g15c_coefficients result = {0};
    uint32_t packed[4];
    if (!in || !out)
        return G15C_INVALID;
    if (in->present != 0xf)
        return G15C_MISSING;
    packed[0] = ((in->w124 >> 30) & 3) | ((in->w128 & 0xfff) << 2);
    packed[1] = (in->w128 >> 12) & 0x3fff;
    packed[2] = ((in->w128 >> 26) & 0x3f) | ((in->w12c & 0xff) << 6);
    packed[3] = (in->w12c >> 8) & 0x3fff;
    for (unsigned lane = 0; lane < 4; lane++)
        result.main_bits[lane] = to_bits(2.0f * (float)(packed[lane] + 1));
    /* Native first-channel producer: (packed13 + 1) * 2 * 0.5. */
    result.cs_bits[0] = to_bits((float)(((in->w134 >> 18) & 0x1fff) + 1));
    result.main_present = 0xf;
    result.cs_present = 1;
    *out = result;
    return G15C_OK;
}

int g15c_23j220_cs_word(uint32_t mv, uint32_t hz, uint32_t leak_bits,
                       uint32_t activity, uint32_t *out)
{
    float leak = from_bits(leak_bits), v, dynamic, total;
    double d;
    int ret = scalar_valid(mv, leak, out);
    if (ret)
        return ret;
    v = div32((float)mv, 1000.0f);
    /* The exponent and coefficient are widened BINARY32 values, not double
     * literals 1.2 and 11.7. The ratio first rounds to binary32 as well. */
    d = mul64(pow((double)v, (double)from_bits(0x3f99999a)),
              (double)from_bits(0x413b3333));
    d = mul64(d, (double)(hz / 1000000));
    d = mul64(d, (double)mul((float)activity, 0.0625f));
    dynamic = (float)d;
    total = add(leakage(v, leak, 1), dynamic);
    return word(mul(v, fminf(total, 37000.0f)), out);
}

int g15c_23j220_main_word(uint32_t mv, uint32_t hz, uint32_t leak_bits,
                         uint32_t uscs, uint32_t cs_word,
                         uint32_t lanes_per_cs, uint32_t *out)
{
    float leak = from_bits(leak_bits), v, dynamic, power;
    int ret = scalar_valid(mv, leak, out);
    if (ret)
        return ret;
    if (!lanes_per_cs)
        return G15C_INVALID;
    v = div32((float)mv, 1000.0f);
    dynamic = mul(powf(v, from_bits(0x3f99999a)), from_bits(0x419f3333));
    dynamic = mul(dynamic, (float)(hz / 1000000));
    dynamic = mul(dynamic, div32((float)uscs, 10.0f));
    power = mul(v, fminf(add(leakage(v, leak, 0), dynamic), 34000.0f));
    return word(add(power, (float)(cs_word / lanes_per_cs)), out);
}

int g15c_23j220_produce(const struct g15c_power_input *in,
                        struct g15c_power_output *out)
{
    struct g15c_power_output result = {0};
    uint32_t lanes_per_cs, denominator;
    float reference = 0;
    int ret;
    if (!in || !out || in->contract != G15C_23J220_CONTRACT ||
        !in->lanes || in->lanes > G15C_MAX_LANES ||
        !in->cs_channels || in->cs_channels > G15C_MAX_CS ||
        in->lanes % in->cs_channels ||
        !in->main_states || in->main_states > G15C_MAX_STATES ||
        !in->cs_states || in->cs_states > G15C_MAX_STATES)
        return G15C_INVALID;
    uint32_t main_mask = (1u << in->lanes) - 1;
    uint32_t cs_mask = (1u << in->cs_channels) - 1;
    if ((in->coefficients.main_present & main_mask) != main_mask ||
        (in->coefficients.cs_present & cs_mask) != cs_mask ||
        (in->uscs_present & main_mask) != main_mask ||
        (in->activity_present & cs_mask) != cs_mask)
        return G15C_MISSING;
    lanes_per_cs = in->lanes / in->cs_channels;
    /* Validate coefficients even when there is only the zeroed state. */
    for (uint32_t lane = 0; lane < in->lanes; lane++) {
        if (!(from_bits(in->coefficients.main_bits[lane]) > 0))
            return G15C_INVALID;
        ret = scalar_valid(0, from_bits(in->coefficients.main_bits[lane]),
                           &result.reference_word);
        if (ret)
            return ret;
    }
    for (uint32_t channel = 0; channel < in->cs_channels; channel++) {
        if (!(from_bits(in->coefficients.cs_bits[channel]) > 0))
            return G15C_INVALID;
        ret = scalar_valid(0, from_bits(in->coefficients.cs_bits[channel]),
                           &result.reference_word);
        if (ret)
            return ret;
        for (uint32_t state = 1; state < in->cs_states; state++) {
            if (!in->cs[state].mv[channel])
                return G15C_MISSING;
            ret = g15c_23j220_cs_word(in->cs[state].mv[channel], in->cs[state].hz,
                    in->coefficients.cs_bits[channel], in->cs_activity[channel],
                    &result.cs_words[state][channel]);
            if (ret)
                return ret;
        }
    }
    for (uint32_t lane = 0; lane < in->lanes; lane++) {
        uint32_t channel = lane / lanes_per_cs;
        for (uint32_t state = 1; state < in->main_states; state++) {
            uint32_t cs_state = state < in->cs_states ? state : in->cs_states - 1;
            if (!in->main[state].mv[lane])
                return G15C_MISSING;
            ret = g15c_23j220_main_word(in->main[state].mv[lane], in->main[state].hz,
                    in->coefficients.main_bits[lane], in->enabled_uscs[lane],
                    result.cs_words[cs_state][channel], lanes_per_cs,
                    &result.lane_words[state][lane]);
            if (ret)
                return ret;
        }
        reference = add(reference, (float)result.lane_words[in->main_states - 1][lane]);
    }
    ret = word(reference, &result.reference_word);
    if (ret)
        return ret;
    for (uint32_t state = 0; state < in->main_states; state++)
        for (uint32_t lane = 0; lane < in->lanes; lane++)
            result.row_sum_u32[state] += result.lane_words[state][lane];
    denominator = result.row_sum_u32[in->main_states - 1];
    for (uint32_t state = 0; state < in->main_states; state++) {
        uint32_t numerator = result.row_sum_u32[state] * 100u;
        /* AArch64 UDIV by zero returns zero. */
        result.relative_percent_u32[state] = denominator ? numerator / denominator : 0;
    }
    result.main_states = in->main_states;
    result.cs_states = in->cs_states;
    result.lanes = in->lanes;
    result.cs_channels = in->cs_channels;
    *out = result;
    return G15C_OK;
}

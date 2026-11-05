/* SPDX-License-Identifier: MIT */
/* Explicit-input, offline 23J220 G15C non-0x17 host arithmetic contract.
 * No discovery, hardware access, physical-unit assertion or live handoff.
 */
#ifndef G15C_23J220_POWER_H
#define G15C_23J220_POWER_H
#include <stdint.h>

#define G15C_MAX_STATES 16
#define G15C_MAX_LANES 4
#define G15C_MAX_CS 2
#define G15C_23J220_CONTRACT 0x23c22001u

enum g15c_status {
    G15C_OK = 0,
    G15C_INVALID = -1,
    G15C_MISSING = -2,
    G15C_NUMERIC = -3,
};

/* Masks indicate supplied values, including a deliberately supplied zero.
 * Bits/floats are never backfilled from lane zero or a stand-in coefficient.
 */
struct g15c_coefficients {
    uint32_t main_bits[G15C_MAX_LANES];
    uint32_t cs_bits[G15C_MAX_CS];
    uint32_t main_present, cs_present;
};

/* Relative old mapped-page source words, already acquired by the caller.
 * This type carries no source address and performs no acquisition.
 */
struct g15c_packed_coefficients {
    uint32_t w124, w128, w12c, w134;
    uint32_t present; /* exactly 0xf: one bit for each named word */
};

struct g15c_main_row { uint32_t hz, mv[G15C_MAX_LANES]; };
struct g15c_cs_row { uint32_t hz, mv[G15C_MAX_CS]; };
struct g15c_power_input {
    uint32_t contract;
    uint32_t main_states, cs_states, lanes, cs_channels;
    struct g15c_coefficients coefficients;
    uint32_t enabled_uscs[G15C_MAX_LANES], uscs_present;
    uint32_t cs_activity[G15C_MAX_CS], activity_present;
    struct g15c_main_row main[G15C_MAX_STATES];
    struct g15c_cs_row cs[G15C_MAX_STATES];
};

struct g15c_power_output {
    uint32_t main_states, cs_states, lanes, cs_channels;
    uint32_t cs_words[G15C_MAX_STATES][G15C_MAX_CS];
    uint32_t lane_words[G15C_MAX_STATES][G15C_MAX_LANES];
    uint32_t row_sum_u32[G15C_MAX_STATES];
    uint32_t relative_percent_u32[G15C_MAX_STATES];
    uint32_t reference_word;
};

/* Decode only the established first four main lanes and FIRST CS channel.
 * Two-CS-channel callers must supply independently sourced float bits instead.
 * Successful decoding does not establish source/branch/board provenance.
 */
int g15c_23j220_decode(const struct g15c_packed_coefficients *,
                       struct g15c_coefficients *);

/* Scalar operations also support isolated CPU oracle comparisons. Supported
 * numerical domain: mV <= 1500, finite nonnegative leakage, u32 frequencies
 * and scalars, nonzero lanes_per_cs, representable final u32 result. This bound
 * follows existing m1n1 input validation, not a physical safe-voltage claim.
 * Requires default IEEE round-to-nearest environment. No output is modified
 * on error. Compile without fast-math; operations round separately (no FMA).
 */
int g15c_23j220_cs_word(uint32_t mv, uint32_t hz, uint32_t leak_bits,
                       uint32_t activity, uint32_t *);
int g15c_23j220_main_word(uint32_t mv, uint32_t hz, uint32_t leak_bits,
                         uint32_t uscs, uint32_t cs_word,
                         uint32_t lanes_per_cs, uint32_t *);
/* The combined producer requires strictly positive active coefficients.
 * A missing/zero coefficient cannot qualify a supplied production input set.
 * Scalar helpers still allow zero for bounded arithmetic-oracle controls.
 */
int g15c_23j220_produce(const struct g15c_power_input *,
                        struct g15c_power_output *);
#endif

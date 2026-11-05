/* SPDX-License-Identifier: MIT */
#ifndef G15C_NATIVE_IMPORT_H
#define G15C_NATIVE_IMPORT_H
#include <stddef.h>
#include <stdint.h>
#include "g15c_23j220_power.h"

/* Ordinary immutable property bytes, not physical addresses or hardware access.
 * Absent optional SRAM is exactly {NULL, 0}; present empty data is malformed.
 * Caller supplies valid readable spans and nonoverlapping output storage.
 */
struct g15c_property { const uint8_t *bytes; size_t length; };
struct g15c_main_import {
    uint32_t count, tables;
    uint32_t hz[16], mv[16][8], sram_mv[16][8];
};
struct g15c_aux_import {
    uint32_t count, channels;
    uint32_t hz[16], mv[16][2], sram_mv[16][2];
};

/* Source-qualified 23J220 positive domain only, with stricter malformed-input
 * refusal. Main count is gpu_num_perf_states+1; no default table count. The last
 * imported table supplies frequencies, including SRAM when present. No units
 * are inferred or converted for main rows. Output is unchanged on refusal.
 */
int g15c_import_main(struct g15c_property main, struct g15c_property sram,
                      uint32_t gpu_num_perf_states, uint32_t tables,
                      struct g15c_main_import *out);
/* CS capacity16, AFR capacity8. Explicit configured channels must match the
 * LE64 header. uV/1000 and Hz narrow to u32 exactly as the source importer does.
 */
int g15c_import_aux(struct g15c_property, uint32_t configured_channels,
                     uint32_t capacity, struct g15c_aux_import *out);
/* Native shared-rail check compares voltages AND SRAM through the shorter
 * table. It does not compare frequencies or require equal state counts.
 */
int g15c_import_shared_rail(const struct g15c_aux_import *, const struct g15c_aux_import *);
#endif

/* SPDX-License-Identifier: MIT */
/* Original integer-only implementation of the reviewed 23J220 import contract. */
#include "native_import.h"

static uint32_t le32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}
static uint64_t le64(const uint8_t *p)
{
    return le32(p) | (uint64_t)le32(p + 4) << 32;
}
static int span(struct g15c_property p, size_t need)
{
    return p.bytes && p.length >= need && (uintptr_t)p.bytes <= UINTPTR_MAX - p.length;
}

int g15c_import_main(struct g15c_property main, struct g15c_property sram,
                      uint32_t n, uint32_t tables, struct g15c_main_import *out)
{
    struct g15c_main_import result = {0};
    if (!out || n < 1 || n > 15 || tables < 1 || tables > 8)
        return G15C_INVALID;
    uint32_t count = n + 1;
    size_t need = (size_t)count * tables * 8;
    int have_sram = sram.bytes != NULL;
    if (!span(main, need) || (!have_sram && sram.length) ||
        (have_sram && !span(sram, need)))
        return G15C_INVALID;
    result.count = count;
    result.tables = tables;
    for (uint32_t pass = 0; pass < (have_sram ? 2u : 1u); pass++) {
        const uint8_t *source = pass ? sram.bytes : main.bytes;
        for (uint32_t table = 0; table < tables; table++) {
            for (uint32_t row = 0; row < count; row++) {
                size_t offset = ((size_t)table * count + row) * 8;
                result.hz[row] = le32(source + offset);
                (pass ? result.sram_mv : result.mv)[row][table] = le32(source + offset + 4);
            }
        }
        uint32_t (*target)[8] = pass ? result.sram_mv : result.mv;
        for (uint32_t row = 0; row < count; row++)
            for (uint32_t lane = 1; lane < 8; lane++)
                if (!target[row][lane])
                    target[row][lane] = target[row][0];
    }
    if (!have_sram)
        for (uint32_t row = 0; row < count; row++)
            for (uint32_t lane = 0; lane < tables; lane++)
                result.sram_mv[row][lane] = result.mv[row][lane] < 789 ? 789 : result.mv[row][0];
    *out = result;
    return G15C_OK;
}

int g15c_import_aux(struct g15c_property source, uint32_t configured_channels,
                     uint32_t capacity, struct g15c_aux_import *out)
{
    struct g15c_aux_import result = {0};
    if (!out || (capacity != 8 && capacity != 16) || !span(source, 16))
        return G15C_INVALID;
    uint64_t channels = le64(source.bytes), count = le64(source.bytes + 8);
    if (channels < 1 || channels > 2 || channels != configured_channels ||
        count < 2 || count > capacity)
        return G15C_INVALID;
    size_t need = 16 + (size_t)channels * ((size_t)count * 16 + 8);
    if (!span(source, need))
        return G15C_INVALID;
    result.count = count;
    result.channels = channels;
    for (uint32_t channel = 0; channel < channels; channel++) {
        size_t floor_offset = 16 + (size_t)channels * count * 16 + channel * 8;
        uint32_t floor = le64(source.bytes + floor_offset) / 1000;
        for (uint32_t row = 0; row < count; row++) {
            size_t offset = 16 + ((size_t)channel * count + row) * 16;
            uint32_t mv = le64(source.bytes + offset) / 1000;
            result.mv[row][channel] = mv;
            result.hz[row] = le64(source.bytes + offset + 8);
            result.sram_mv[row][channel] = mv > floor ? mv : floor;
        }
    }
    *out = result;
    return G15C_OK;
}

int g15c_import_shared_rail(const struct g15c_aux_import *cs, const struct g15c_aux_import *afr)
{
    if (!cs || !afr || cs->channels < 1 || cs->channels > 2 ||
        cs->channels != afr->channels || cs->count < 2 || cs->count > 16 ||
        afr->count < 2 || afr->count > 8)
        return G15C_INVALID;
    uint32_t count = cs->count < afr->count ? cs->count : afr->count;
    for (uint32_t row = 0; row < count; row++)
        for (uint32_t channel = 0; channel < cs->channels; channel++)
            if (cs->mv[row][channel] != afr->mv[row][channel] ||
                cs->sram_mv[row][channel] != afr->sram_mv[row][channel])
                return G15C_INVALID;
    return G15C_OK;
}

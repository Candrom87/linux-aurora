/* SPDX-License-Identifier: MIT */
/* Original explicit-input bridge, no device access or physical-unit claim. */
#include "board_source.h"
#define EXPORT __attribute__((visibility("default")))

static uint32_t population(uint32_t word)
{
    uint32_t count = 0;
    for (uint32_t i = 0; i < 32; i++)
        count += (word >> i) & 1;
    return count;
}

/* Keep the large main import out of the packet/auxiliary stack frame. */
static __attribute__((noinline)) int main_source(const struct g15c_board_source_input *in,
                                                struct g15c_board_packet_input *result)
{
    struct g15c_main_import main;
    int status = g15c_import_main(in->main, in->sram, in->gpu_num_perf_states,
                                in->table_count, &main);
    if (status)
        return status;
    result->source.main_states = main.count;
    for (uint32_t row = 0; row < main.count; row++) {
        result->source.main[row].hz = main.hz[row];
        for (uint32_t lane = 0; lane < 4; lane++)
            result->source.main[row].mv[lane] = main.mv[row][lane];
    }
    return G15C_OK;
}

EXPORT int g15c_23j220_board_source(const struct g15c_board_source_input *in,
                                    struct g15c_board_packet_input *out)
{
    struct g15c_board_packet_input result = {0};
    if (!in || !out || in->contract != G15C_23J220_CONTRACT || in->chip_id != 0x6034 ||
        in->table_count != 4 || in->gpu_num_perf_states < 1 ||
        in->gpu_num_perf_states >= G15C_BOARD_RAW_STATES)
        return G15C_INVALID;
    uint32_t version = in->identity[0], counts = in->identity[1];
    if ((version >> 24) != 7 || ((version >> 16) & 255) != 4 ||
        ((counts >> 16) & 15) != 1 || ((counts >> 8) & 255) != 4 || (counts & 255) != 10 ||
        !(in->identity[3] | in->identity[4]) || in->identity[4] >= 256 || in->identity[5])
        return G15C_INVALID;
    if (in->masks_present != 7)
        return G15C_MISSING;
    int status;
    status = main_source(in, &result);
    if (status)
        return status;
    {
        struct g15c_aux_import cs, afr;
        if ((status = g15c_import_aux(in->cs, 1, 16, &cs)) ||
            (status = g15c_import_aux(in->afr, 1, 8, &afr)) ||
            (status = g15c_import_shared_rail(&cs, &afr)))
            return status;
        result.source.cs_states = cs.count;
        for (uint32_t row = 0; row < cs.count; row++) {
            result.source.cs[row].hz = cs.hz[row];
            result.source.cs[row].mv[0] = cs.mv[row][0];
        }
    }
    /* Only the established first CS coefficient is available in this packed decode. */
    status = g15c_23j220_decode(&in->coefficients, &result.source.coefficients);
    if (status)
        return status;
    result.source.contract = G15C_23J220_CONTRACT;
    result.source.lanes = 4;
    result.source.cs_channels = 1;
    result.source.uscs_present = 15;
    result.source.activity_present = 1;
    result.source.cs_activity[0] = population(in->cs_activity_mask);
    uint64_t mask = in->identity[3] | (uint64_t)in->identity[4] << 32;
    for (uint32_t lane = 0; lane < 4; lane++)
        result.source.enabled_uscs[lane] = population((mask >> (lane * 10)) & 1023);
    result.chip_id = in->chip_id;
    result.masks_present = in->masks_present;
    for (uint32_t bank = 0; bank < 2; bank++)
        for (uint32_t word = 0; word < 8; word++)
            result.map_banks[bank][word] = in->map_banks[bank][word];
    for (uint32_t mask_index = 0; mask_index < 3; mask_index++)
        for (uint32_t word = 0; word < 4; word++)
            result.masks_msb[mask_index][word] = in->masks_msb[mask_index][word];
    *out = result;
    return G15C_OK;
}

/* SPDX-License-Identifier: MIT */
/* Original implementation of the pinned 23J220 selector contract. */
#include "native_select.h"
#include "g15c_23j220_power.h"

int g15c_native_select(const uint32_t banks[2][8], uint32_t logical_count,
                      struct g15c_native_selection *out)
{
    struct g15c_native_selection result = {.primary_count = 1, .secondary_count = 1};
    if (!banks || !out || logical_count < 1 || logical_count > 16)
        return G15C_INVALID;
    /* Entry zero is ignored; each stream begins with the explicit zero row. */
    for (uint32_t entry = 1; entry < 16; entry++) {
        uint32_t shift = (entry & 1) * 16;
        uint32_t p = (banks[0][entry / 2] >> shift) & 15;
        uint32_t s = (banks[1][entry / 2] >> shift) & 15;
        if (p > logical_count)
            break;
        if (p)
            result.primary[result.primary_count++] = p;
        if (s)
            result.secondary[result.secondary_count++] = s;
    }
    *out = result;
    return G15C_OK;
}

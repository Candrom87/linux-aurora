/* SPDX-License-Identifier: MIT */
#ifndef G15C_NATIVE_SELECT_H
#define G15C_NATIVE_SELECT_H
#include <stdint.h>
/* Internal integer selector. Selection itself retains the native > boundary;
 * materialization separately refuses indices outside the actual row array.
 */
struct g15c_native_selection {
    uint32_t primary_count, secondary_count;
    uint32_t primary[16], secondary[16];
};
int g15c_native_select(const uint32_t banks[2][8], uint32_t logical_count,
                      struct g15c_native_selection *);
#endif

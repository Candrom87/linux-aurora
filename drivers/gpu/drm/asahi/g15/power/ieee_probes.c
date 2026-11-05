/* SPDX-License-Identifier: MIT */
#include <stdint.h>
#include <string.h>

int g15c_port_ieee_probes(void)
{
    uint32_t fb = 0x00800000, fi;
    uint64_t db = 0x0010000000000000ull, di;
    float f;
    double d;
    memcpy(&f, &fb, sizeof(f));
    memcpy(&d, &db, sizeof(d));
    volatile float fn = f, half = 0.5f, two = 2.0f, one = 1.0f, ftie = 0x1p-24f;
    volatile double dn = d, dhalf = 0.5, dtwo = 2.0, done = 1.0, dtie = 0x1p-53;
    volatile float fs = fn * half;
    volatile double ds = dn * dhalf;
    f = fs; d = ds;
    memcpy(&fi, &f, sizeof(fi)); memcpy(&di, &d, sizeof(di));
    if (fi != 0x00400000 || di != 0x0008000000000000ull)
        return 0;
    f = fs * two; d = ds * dtwo;
    memcpy(&fi, &f, sizeof(fi)); memcpy(&di, &d, sizeof(di));
    if (fi != fb || di != db)
        return 0;
    f = one + ftie; d = done + dtie;
    memcpy(&fi, &f, sizeof(fi)); memcpy(&di, &d, sizeof(di));
    return fi == 0x3f800000 && di == 0x3ff0000000000000ull;
}

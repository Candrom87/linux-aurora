/* SPDX-License-Identifier: MIT */
#include "port.h"

int g15c_port_fpcr_accepted(uint64_t value)
{
    /* Deliberately narrow candidate policy, not a claim that every nonzero
     * bit changes this binary32/binary64 model. In particular FP16-only/default
     * NaN behavior is not used by the admitted finite positive input domain.
     * Canonical zero also excludes rounding/flush/exception-trap overrides.
     */
    return value == 0;
}

int g15c_port_ieee_environment(void)
{
    if (!g15c_port_fpcr_accepted(g15c_port_read_fpcr()))
        return 0;
    /* Separate FP translation unit, no LTO: never execute arithmetic probes
     * before the integer-only architectural control guard has succeeded.
     * Probes bound flushing/rounding behavior without reading Apple AFPCR.
     */
    if (!g15c_port_ieee_probes())
        return 0;
    return g15c_port_fpcr_accepted(g15c_port_read_fpcr());
}

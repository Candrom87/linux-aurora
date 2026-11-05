/* SPDX-License-Identifier: MIT */
#include <stdint.h>
/* Architectural CPU-local read only. No MMIO, privileged vendor register or
 * control/status-register write. This file requires AArch64 FP access enabled.
 */
uint64_t g15c_port_read_fpcr(void)
{
    uint64_t value;
    __asm__ volatile("mrs %0, S3_3_C4_C4_0" : "=r"(value) :: "memory");
    return value;
}

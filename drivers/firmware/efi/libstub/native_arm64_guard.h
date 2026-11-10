/* SPDX-License-Identifier: MIT */
#ifndef NATIVE_ARM64_GUARD_H
#define NATIVE_ARM64_GUARD_H
#define NG_OK 0
#define NG_ARGUMENT 1
#define NG_PHASE 2
#define NG_CPU 3
#define NG_TRANSLATION 4
#define NG_ATTRIBUTES 5
#define NG_ABORT 6
#define NG_LATCHED 7
#define NG_READ32 0
#define NG_READ64 1
#define NG_WRITE32 2
#define NG_DEVICE 0
#define NG_RAM_WB 1
#ifndef __ASSEMBLER__
#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdbool.h>
#include <stdint.h>
#endif
#define NG_COUNT_MAX ((uint32_t)~0U)
struct native_guard_phase { bool active, faulted; uint32_t issued, completed; };
struct native_guard_span { uint64_t base, bytes; unsigned kind; bool write; };

/* Privileged EL2, post-ExitBootServices ONLY. Caller must have actually ended
 * boot services, own its stack/code/data, and keep CPUs/translation state under
 * its control. services_exited is a trusted coordinator contract, not proof.
 * On success installs permanent native exception vectors and masks D/I/F;
 * SError stays unmasked. Returning to EFI is forbidden. No EFI calls are made.
 * This low-level module does not grant device ownership/power/firmware admission.
 */
int native_guard_begin(struct native_guard_phase *, bool services_exited);

/* A source-validated permit must precede this call. Checks existing identity
 * translation and exact Device-nGnRnE / normal-WB attributes; changes no mapping.
 * Natural 4/8-byte alignment, width, range and write policy are enforced.
 * Reads leave value unchanged on refusal. Any access refusal latches the phase.
 * Only precise address/translation/access/permission aborts at the controlled
 * instruction can return NG_ABORT. External aborts, SError and other exceptions
 * stop in native vectors; bus stalls have no software timeout guarantee.
 */
int native_guard_access(struct native_guard_phase *, const struct native_guard_span *,
                        unsigned operation, uint64_t address, uint64_t *value);
#endif
#endif

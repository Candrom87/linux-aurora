/* SPDX-License-Identifier: MIT */
#include "native_arm64_guard.h"
extern int native_arm64_takeover(void);
extern int native_arm64_raw_access(uint64_t, uint64_t *, unsigned);

int native_guard_begin(struct native_guard_phase *phase, bool services_exited)
{
    if (!phase || !services_exited || phase->active || phase->faulted) return NG_PHASE;
    int status = native_arm64_takeover();
    if (!status) { phase->active = true; phase->issued = phase->completed = 0; }
    return status;
}

static uint64_t translate(uint64_t address, bool write)
{
    uint64_t saved, result;
    __asm__ volatile("mrs %0, par_el1" : "=r"(saved));
    if (write) __asm__ volatile("at s1e2w, %0" :: "r"(address) : "memory");
    else __asm__ volatile("at s1e2r, %0" :: "r"(address) : "memory");
    __asm__ volatile("isb\n\tmrs %0, par_el1" : "=r"(result) :: "memory");
    __asm__ volatile("msr par_el1, %0\n\tisb" :: "r"(saved) : "memory");
    return result;
}

int native_guard_access(struct native_guard_phase *phase, const struct native_guard_span *permit,
                        unsigned operation, uint64_t address, uint64_t *value)
{
    const uint64_t limit = 1ULL << 48;
    uint64_t width = operation == NG_READ64 ? 8 : 4, par;
    int status = NG_ARGUMENT;
    if (!phase || !phase->active) return NG_PHASE;
    if (phase->faulted) return NG_LATCHED;
    if (!permit || !value || operation > NG_WRITE32 || permit->kind > NG_RAM_WB ||
        !permit->base || permit->base >= limit || !permit->bytes || permit->bytes > limit - permit->base ||
        address % width || address < permit->base || width > permit->bytes ||
        address - permit->base > permit->bytes - width || phase->issued == NG_COUNT_MAX ||
        (operation == NG_WRITE32 && (!permit->write || permit->kind != NG_DEVICE || *value > NG_COUNT_MAX)))
        goto fail;
    par = translate(address, operation == NG_WRITE32);
    if ((par & 1) || (par & 0x000ffffffffff000ULL) != (address & ~0xfffULL)) {
        status = NG_TRANSLATION; goto fail;
    }
    if ((par >> 56) != (permit->kind == NG_DEVICE ? 0U : 0xffU) ||
        (permit->kind == NG_RAM_WB && ((par >> 7) & 3) != 3)) {
        status = NG_ATTRIBUTES; goto fail;
    }
    phase->issued++;
    status = native_arm64_raw_access(address, value, operation);
    if (status) goto fail;
    phase->completed++;
    return NG_OK;
fail:
    phase->faulted = true;
    return status;
}

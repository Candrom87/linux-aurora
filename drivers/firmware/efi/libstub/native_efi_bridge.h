/* SPDX-License-Identifier: MIT */
#ifndef M3MAX_NATIVE_EFI_BRIDGE_H
#define M3MAX_NATIVE_EFI_BRIDGE_H
#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdint.h>
#include <stdbool.h>
#endif

/* Private, versioned ABI between one kernel and its embedded EFI application.
 * The application calls the kernel continuation WITHOUT returning to StartImage.
 * Its LoaderCode/Data and active EFI entry stack therefore survive until the
 * kernel enters. No resident driver or stale EFI Exit() jump target is used.
 * The two binaries must be built/reviewed together. This is not a firmware ABI.
 */
#define NE_MAGIC 0x31454649584d334eULL
#define NE_PREPARED_MAGIC 0x31504452584d334eULL
#define NE_VERSION 1
#define NE_PROFILE_METADATA 1
#define NE_PROFILE_FULL 2
#define NE_PROFILE_BOOT_MEMORY 3
#define NE_CAP_METADATA (1ULL << 0)
#define NE_CAP_POSTEFI (1ULL << 1)
#define NE_CAP_MCC (1ULL << 2)
#define NE_CAP_DISPLAY (1ULL << 3)
#define NE_CAP_GPU (1ULL << 4)
#define NE_CAP_ORIGINAL_ARGS (1ULL << 5)
#define NE_CAP_BOOT_MEMORY (1ULL << 6)
#define NE_BOOT_MEMORY_CAPS (NE_CAP_METADATA | NE_CAP_BOOT_MEMORY)
#define NE_BOOT_MEMORY_PROPERTY "asahi,t6034-boot-memory-audit"
#define NE_BOOT_MEMORY_VERSION 1
#define NE_BOOT_MEMORY_CELLS 10
#define NE_FULL_CAPS (NE_CAP_METADATA | NE_CAP_POSTEFI | NE_CAP_MCC | \
                      NE_CAP_DISPLAY | NE_CAP_GPU | NE_CAP_ORIGINAL_ARGS)
#define NE_FDT_MAX (2U * 1024U * 1024U)
#define NE_IMAGE_MAX (16U * 1024U * 1024U)

struct ne_launch;
struct ne_prepared;
typedef uint64_t (*ne_continue_fn)(struct ne_launch *, const struct ne_prepared *);
typedef uint64_t (*ne_post_fn)(const struct ne_prepared *, void *, uint32_t);
typedef void (*ne_stop_fn)(uint32_t);

struct ne_launch {
    uint64_t magic;
    uint32_t version, bytes;
    uint32_t profile, reserved;
    uint64_t required_caps;
    void *parent_handle, *child_handle;
    uint64_t parent_base, parent_bytes, child_base, child_bytes;
    void *caller_context;
    ne_continue_fn continue_boot;
};

struct ne_prepared {
    uint64_t magic;
    uint32_t version, bytes;
    uint32_t profile, reserved;
    uint64_t caps;
    const void *adt;
    uint32_t adt_span, adt_tree_bytes;
    const void *fdt;
    uint32_t fdt_bytes, fdt_capacity;
    void *native_context;
    ne_post_fn post_exit;
    ne_stop_fn fail_stop;
};

static inline bool ne_range(uint64_t base, uint64_t bytes)
{
    return base && bytes && base <= ~0ULL - bytes;
}

static inline bool ne_code(uint64_t base, uint64_t bytes, uint64_t fn)
{
    return ne_range(base, bytes) && !(fn & 3) && fn >= base &&
           bytes >= 4 && fn - base <= bytes - 4;
}

static inline uint64_t ne_profile_caps(uint32_t profile)
{
    switch (profile) {
    case NE_PROFILE_METADATA: return NE_CAP_METADATA;
    case NE_PROFILE_FULL: return NE_FULL_CAPS;
    case NE_PROFILE_BOOT_MEMORY: return NE_BOOT_MEMORY_CAPS;
    default: return 0;
    }
}

static inline bool ne_launch_valid(const struct ne_launch *p)
{
    return p && p->magic == NE_MAGIC && p->version == NE_VERSION &&
           p->bytes == sizeof(*p) && !p->reserved && p->parent_handle &&
           p->child_handle && p->caller_context &&
           p->required_caps && p->required_caps == ne_profile_caps(p->profile) &&
           p->child_bytes <= NE_IMAGE_MAX &&
           ne_range(p->child_base, p->child_bytes) &&
           ne_code(p->parent_base, p->parent_bytes, (uint64_t)p->continue_boot);
}

static inline bool ne_prepared_valid(const struct ne_launch *l, const struct ne_prepared *p)
{
    if (!ne_launch_valid(l) || !p || p->magic != NE_PREPARED_MAGIC ||
        p->version != NE_VERSION || p->bytes != sizeof(*p) || p->reserved ||
        p->profile != l->profile || p->caps != l->required_caps ||
        !ne_range((uint64_t)p->adt, p->adt_span) || !p->adt_tree_bytes ||
        p->adt_tree_bytes > p->adt_span || p->adt_span > NE_FDT_MAX ||
        !ne_range((uint64_t)p->fdt, p->fdt_capacity) || !p->fdt_bytes ||
        p->fdt_bytes > p->fdt_capacity || p->fdt_capacity > NE_FDT_MAX ||
        ((uint64_t)p->fdt & 7) || !p->native_context)
        return false;
    if ((uint64_t)p->adt < (uint64_t)p->fdt + p->fdt_capacity &&
        (uint64_t)p->fdt < (uint64_t)p->adt + p->adt_span)
        return false;
    if (l->profile != NE_PROFILE_FULL)
        return !p->post_exit && !p->fail_stop;
    return ne_code(l->child_base, l->child_bytes, (uint64_t)p->post_exit) &&
           ne_code(l->child_base, l->child_bytes, (uint64_t)p->fail_stop);
}

_Static_assert(sizeof(void *) == 8, "AArch64 EFI ABI only");
_Static_assert(sizeof(struct ne_launch) == 96, "launch ABI layout");
_Static_assert(sizeof(struct ne_prepared) == 88, "prepared ABI layout");
#endif

/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
#ifndef ARM64_ASAHI_GPU_MEMORY_H
#define ARM64_ASAHI_GPU_MEMORY_H
#include <linux/init.h>
#include <linux/types.h>

#define AGM_MAX_BLOB (2U * 1024U * 1024U)
struct agm_span { u64 base, size; };
struct agm_layout { struct agm_span dram, uat[4]; };

/* CPU-buffer operations only. Output structures are unchanged on refusal.
 * Boot metadata is trusted input, not a hardware/firmware authentication root.
 */
int agm_adt_layout(const void *blob, size_t size, struct agm_layout *out);
int agm_fdt_source(const void *blob, size_t size, struct agm_span *out);
/* Scratch may change on refusal; original bytes never change. */
int agm_fdt_plan(const void *blob, size_t size, void *scratch, size_t capacity,
		 const struct agm_layout *layout);

#ifdef CONFIG_APPLE_T6034_GPU_EARLY_MEMORY
void __init asahi_gpu_early_memory(bool unrestricted, u64 linear_size);
void __init asahi_gpu_publish_memory(void);
#else
static inline void asahi_gpu_early_memory(bool unrestricted, u64 linear_size) {}
static inline void asahi_gpu_publish_memory(void) {}
#endif
#endif

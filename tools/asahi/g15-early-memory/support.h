/* SPDX-License-Identifier: MIT */
/* Synthetic host kernel services. Never map a device or physical memory. */
#ifndef AGM_TEST_SUPPORT_H
#define AGM_TEST_SUPPORT_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
#include <stdarg.h>
typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef uint64_t phys_addr_t;
typedef uint32_t __be32;
typedef int64_t s64;
#define __init
#define __initdata
#define __aligned(n) __attribute__((aligned(n)))
#define CONFIG_APPLE_T6034_GPU_EARLY_MEMORY 1
#define U64_MAX UINT64_MAX
#define U32_MAX UINT32_MAX
#define PHYS_MASK ((UINT64_C(1) << 48) - 1)
#ifndef PAGE_SHIFT
#define PAGE_SHIFT 14
#endif
#define PAGE_SIZE (1UL << PAGE_SHIFT)
#define PAGE_ALIGN(x) (((x) + PAGE_SIZE - 1) & ~(PAGE_SIZE - 1))
#define PAGE_KERNEL_RO 1
#define EFI_BOOT 1
#define EFI_PAGE_SHIFT 12
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#define early_param(name, fn) static void *const used_param __attribute__((used)) = (void *)fn
#define pr_warn(...) test_log(__VA_ARGS__)
#define pr_info(...) test_log(__VA_ARGS__)
#define pr_err(...) test_log(__VA_ARGS__)
#define pr_debug(...) test_log(__VA_ARGS__)
#define SZ_1M (1024UL * 1024UL)
#define MAX_RESERVED_REGIONS 64
#define be32_to_cpup(p) __builtin_bswap32(*(p))
struct memblock_region { u64 base, size; bool nomap; };
struct memblock_type { struct memblock_region regions[64]; unsigned int cnt; };
struct memblock { struct memblock_type memory, reserved; };
extern struct memblock memblock;
extern s64 memstart_addr;
extern void *initial_boot_params;
typedef struct { u32 type, pad; u64 phys_addr, virt_addr, num_pages, attribute; } efi_memory_desc_t;
struct test_efi_map { void *map, *map_end; unsigned long desc_size; int nr_map; };
struct test_efi { struct test_efi_map memmap; };
extern struct test_efi efi;
#define for_each_reserved_mem_region(r) \
 for ((r) = memblock.reserved.regions; (r) < memblock.reserved.regions + memblock.reserved.cnt; (r)++)
#define for_each_efi_memory_desc(md) \
 for ((md) = efi.memmap.map; (md) && (void *)((char *)(md) + efi.memmap.desc_size) <= efi.memmap.map_end; \
      (md) = (void *)((char *)(md) + efi.memmap.desc_size))
static inline u32 get_unaligned_le32(const void *p)
{ const u8 *s = p; return (u32)s[0] | (u32)s[1] << 8 | (u32)s[2] << 16 | (u32)s[3] << 24; }
static inline u64 get_unaligned_le64(const void *p)
{ return (u64)get_unaligned_le32(p) | (u64)get_unaligned_le32((const u8 *)p + 4) << 32; }
void test_log(const char *fmt, ...);
void panic(const char *message) __attribute__((noreturn));
bool efi_enabled(int feature);
bool memblock_overlaps_region(struct memblock_type *type, u64 base, u64 size);
bool memblock_is_region_memory(u64 base, u64 size);
bool memblock_is_map_memory(u64 base);
int memblock_reserve(u64 base, u64 size);
int memblock_add(u64 base, u64 size);
int memblock_mark_nomap(u64 base, u64 size);
bool memblock_is_region_reserved(u64 base, u64 size);
u64 memblock_phys_alloc(u64 size, u64 alignment);
int memblock_phys_free(u64 base, u64 size);
int copy_from_early_mem(void *dest, u64 source, unsigned long size);
void *early_memremap(u64 dest, unsigned long size);
void early_memunmap(void *map, unsigned long size);
void *fixmap_remap_fdt(u64 phys, int *size, int protection);
bool early_init_dt_verify(void *fdt, u64 phys);
void early_init_fdt_scan_reserved_mem(void);
#endif

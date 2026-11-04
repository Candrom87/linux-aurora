/* SPDX-License-Identifier: MIT */
#include "support.h"
#include <setjmp.h>
#include <libfdt.h>
#include "asahi-gpu-memory.h"

#define DRAM UINT64_C(0x10000000)
#define USABLE (DRAM + 0x400000)
#define SOURCE (DRAM + 0x500000)
#define NEW_TREE (DRAM + 0x600000)
struct memblock memblock;
s64 memstart_addr;
void *initial_boot_params;
struct test_efi efi;
static efi_memory_desc_t descriptors[8];
static unsigned char synthetic_tree[AGM_MAX_BLOB] __attribute__((aligned(8)));
static const void *adt_data;
static size_t adt_size;
static void *original_tree;
static unsigned int gpu_reserves, gpu_adds, copy_reads, map_calls, frees, logs, scans;
static int fault, fault_at, order_error, efi_boot;
static jmp_buf panic_target;
static const char *panic_message;
static int total_reserved_mem_cnt;
static const int dt_root_size_cells = 2, dt_root_addr_cells = 2;

/* Exact production runtime, with the services declared above simulated. */
#include "actual_runtime.c"

static const void *of_get_flat_dt_prop(unsigned long node, const char *name, int *length)
{ return fdt_getprop(initial_boot_params, (int)node, name, length); }
static const __be32 *of_flat_dt_get_addr_size_prop(unsigned long node, const char *name, int *entries)
{
	int length;
	const void *p = of_get_flat_dt_prop(node, name, &length);
	if (!p || length % 16) return NULL;
	*entries = length / 16; return p;
}
static void of_flat_dt_read_addr_size(const __be32 *p, int index, u64 *base, u64 *size)
{ const fdt64_t *v = (const void *)p; *base = fdt64_ld(v + index * 2); *size = fdt64_ld(v + index * 2 + 1); }
static int fdt_validate_reserved_mem_node(unsigned long node, phys_addr_t *align)
{ (void)node; (void)align; return -ENODEV; }
static int fdt_fixup_reserved_mem_node(unsigned long node, phys_addr_t base, phys_addr_t size)
{ (void)node; (void)base; (void)size; return -ENODEV; }
static bool of_fdt_device_is_available(const void *tree, int node)
{
	int length;
	const char *p = fdt_getprop(tree, node, "status", &length);
	return !p || (length == 5 && !memcmp(p, "okay", 5)) || (length == 3 && !memcmp(p, "ok", 3));
}
static int __reserved_mem_alloc_size(unsigned long node, const char *name)
{ (void)node; (void)name; abort(); }
#include "actual_scan.inc"

static void insert(struct memblock_type *t, u64 base, u64 size)
{
	unsigned int at = 0;

	if (t->cnt >= 64) abort();
	while (at < t->cnt && t->regions[at].base < base) at++;
	memmove(t->regions + at + 1, t->regions + at, (t->cnt - at) * sizeof(t->regions[0]));
	t->regions[at] = (struct memblock_region){ base, size, false };
	t->cnt++;
}

void test_log(const char *fmt, ...) { (void)fmt; logs++; }
void panic(const char *message) { panic_message = message; longjmp(panic_target, 1); }
bool efi_enabled(int feature) { return feature == EFI_BOOT && efi_boot; }
bool memblock_overlaps_region(struct memblock_type *t, u64 base, u64 size)
{
	for (unsigned int i = 0; i < t->cnt; i++)
		if (base < t->regions[i].base + t->regions[i].size && t->regions[i].base < base + size)
			return true;
	return false;
}
bool memblock_is_region_memory(u64 base, u64 size)
{
	for (unsigned int i = 0; i < memblock.memory.cnt; i++) {
		struct memblock_region r = memblock.memory.regions[i];
		if (base >= r.base && base + size <= r.base + r.size) return true;
	}
	return false;
}
bool memblock_is_map_memory(u64 base)
{
	for (unsigned int i = 0; i < memblock.memory.cnt; i++) {
		struct memblock_region r = memblock.memory.regions[i];
		if (base >= r.base && base < r.base + r.size) return !r.nomap;
	}
	return false;
}
int memblock_reserve(u64 base, u64 size)
{
	if (base < USABLE) {
		gpu_reserves++;
		if (fault == 1 && gpu_reserves == (unsigned int)fault_at) return -ENOMEM;
	}
	insert(&memblock.reserved, base, size);
	return 0;
}
int memblock_add(u64 base, u64 size)
{
	if (base < USABLE) {
		gpu_adds++;
		if (gpu_reserves != 4) order_error = 1;
		if (fault == 2 && gpu_adds == (unsigned int)fault_at) return -ENOMEM;
	}
	insert(&memblock.memory, base, size);
	return 0;
}
int memblock_mark_nomap(u64 base, u64 size)
{
	for (unsigned int i = 0; i < memblock.memory.cnt; i++)
		if (base < memblock.memory.regions[i].base + memblock.memory.regions[i].size &&
		    memblock.memory.regions[i].base < base + size) memblock.memory.regions[i].nomap = true;
	return 0;
}
bool memblock_is_region_reserved(u64 base, u64 size)
{ return memblock_overlaps_region(&memblock.reserved, base, size); }
u64 memblock_phys_alloc(u64 size, u64 alignment)
{
	if (size > sizeof(synthetic_tree) || alignment != PAGE_SIZE) abort();
	if (fault == 3) return 0;
	insert(&memblock.reserved, NEW_TREE, size);
	return NEW_TREE;
}
int memblock_phys_free(u64 base, u64 size)
{
	if (base != NEW_TREE) abort();
	frees++;
	for (unsigned int i = 0; i < memblock.reserved.cnt; i++)
		if (memblock.reserved.regions[i].base == base && memblock.reserved.regions[i].size == size) {
			memmove(memblock.reserved.regions + i, memblock.reserved.regions + i + 1,
				(memblock.reserved.cnt - i - 1) * sizeof(memblock.reserved.regions[0]));
			memblock.reserved.cnt--; return 0;
		}
	abort();
}
int copy_from_early_mem(void *dest, u64 source, unsigned long size)
{
	copy_reads++;
	if (source != SOURCE || size != 0x4000 || adt_size > size) abort();
	if (fault == 4) return -ENOMEM;
	memset(dest, 0, size); memcpy(dest, adt_data, adt_size); return 0;
}
void *early_memremap(u64 dest, unsigned long size)
{
	map_calls++;
	if (dest < NEW_TREE || dest + size > NEW_TREE + sizeof(synthetic_tree)) abort();
	if (fault == 5 && map_calls == (unsigned int)fault_at) return NULL;
	return synthetic_tree + (dest - NEW_TREE);
}
void early_memunmap(void *map, unsigned long size) { (void)map; (void)size; }
void *fixmap_remap_fdt(u64 phys, int *size, int protection)
{
	if (phys != NEW_TREE || protection != PAGE_KERNEL_RO) abort();
	if (fault == 6) return NULL;
	*size = fdt_totalsize(synthetic_tree); return synthetic_tree;
}
bool early_init_dt_verify(void *fdt, u64 phys)
{
	if (phys != NEW_TREE || fdt != synthetic_tree) abort();
	if (fault == 7) return false;
	initial_boot_params = fdt; return true;
}
void early_init_fdt_scan_reserved_mem(void)
{
	/* Run unchanged source bodies. Only their services/callbacks are modeled.
	 * Duplicate static reserve requests are normal and not new GPU commit ops.
	 */
	unsigned int saved_reserves = gpu_reserves;
	if (fdt_scan_reserved_mem()) abort();
	gpu_reserves = saved_reserves;
	scans++;
	if (fault == 8) insert(&memblock.memory, DRAM + 0x100000, 0x4000);
	if (fault == 9) memblock.reserved.cnt = 0;
	if (fault == 10)
		for (unsigned int i = 0; i < memblock.memory.cnt; i++)
			if (memblock.memory.regions[i].base < USABLE) memblock.memory.regions[i].nomap = true;
}

void fixture_reset(void *tree, const void *raw, size_t raw_size);
void fixture_control(int kind, u64 value);
int fixture_request(const char *arg);
int fixture_run(int unrestricted, u64 linear_size);
u64 fixture_query(int kind);
void *fixture_tree(void);
void fixture_reset(void *tree, const void *raw, size_t raw_size)
{
	memset(&memblock, 0, sizeof(memblock));
	memset(descriptors, 0, sizeof(descriptors));
	memset(synthetic_tree, 0, sizeof(synthetic_tree));
	insert(&memblock.memory, USABLE, 0x800000);
	insert(&memblock.reserved, SOURCE, 0x4000);
	descriptors[0] = (efi_memory_desc_t){ .type = 7, .phys_addr = USABLE, .num_pages = 0x800 };
	efi.memmap = (struct test_efi_map){ descriptors, descriptors + 1, sizeof(descriptors[0]), 1 };
	adt_data = raw; adt_size = raw_size; initial_boot_params = original_tree = tree;
	memstart_addr = DRAM; requested = 0; fault = fault_at = order_error = 0;
	gpu_reserves = gpu_adds = copy_reads = map_calls = frees = logs = scans = 0;
	efi_boot = 1; panic_message = NULL;
	total_reserved_mem_cnt = 1;
}
void fixture_control(int kind, u64 value)
{
	switch (kind) {
	case 1: fault = (int)value; break;
	case 2: fault_at = (int)value; break;
	case 3: descriptors[1] = (efi_memory_desc_t){ .type = (u32)value, .phys_addr = DRAM + 0x100000, .num_pages = 4 };
		efi.memmap.map_end = descriptors + 2; efi.memmap.nr_map = 2; break;
	case 4: memblock.reserved.cnt = 0; break;
	case 5: memblock.reserved.regions[0].size = value; break;
	case 6: memblock.memory.regions[0].nomap = true; break;
	case 7: insert(&memblock.memory, DRAM + 0x100000, 0x4000); break;
	case 8: insert(&memblock.reserved, DRAM + 0x100000, 0x4000); break;
	case 9: memstart_addr = (s64)value; break;
	case 10: efi_boot = (int)value; break;
	case 11: descriptors[0].num_pages = value; break;
	case 12: descriptors[0].phys_addr = value; break;
	case 13: efi.memmap.desc_size = (unsigned long)value; break;
	case 14: efi.memmap.map_end = descriptors; break;
	default: abort();
	}
}
int fixture_request(const char *arg) { return request_memory((char *)arg); }
int fixture_run(int unrestricted, u64 linear_size)
{
	if (setjmp(panic_target)) return 1;
	asahi_gpu_early_memory(unrestricted, linear_size); return 0;
}
u64 fixture_query(int kind)
{
	switch (kind) {
	case 1: return gpu_reserves;
	case 2: return gpu_adds;
	case 3: return copy_reads;
	case 4: return map_calls;
	case 5: return frees;
	case 6: return logs;
	case 7: return scans;
	case 8: return order_error;
	case 9: return initial_boot_params != original_tree;
	case 10: return memblock_overlaps_region(&memblock.memory, DRAM + 0x100000, 0x4000);
	case 11: return panic_message != NULL;
	case 12: return (u64)total_reserved_mem_cnt;
	default: abort();
	}
}
void *fixture_tree(void) { return initial_boot_params; }

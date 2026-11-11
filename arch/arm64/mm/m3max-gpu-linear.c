// SPDX-License-Identifier: GPL-2.0-only
/* Isolated native EFI test kernels only. No GPU payload or MMIO access. */
#include <linux/efi.h>
#include <linux/init.h>
#include <linux/initrd.h>
#include <linux/libfdt.h>
#include <linux/m3max-gpu-linear.h>
#include <linux/m3max-gpu-linear-schema.h>
#include <linux/memblock.h>
#include <linux/mm.h>
#include <linux/of_fdt.h>
#include <linux/overflow.h>
#include <asm/memory.h>
#include <asm/setup.h>
#include <asm/sections.h>

struct m3g_range { u64 base, bytes; };
static const char * const names[] = {
	"ttbs", "pagetables", "handoff", "pagetables-l2", "fw-text", "fw-data",
};
static const char * const owners[] = {
	"uat-ttbs", "uat-pagetables", "uat-handoff", "uat-pagetables-l2",
};
static const u64 uat_bytes[] = { 0x4000, 0x40000, 0x4000, 0x4000 };
static struct m3g_range admitted[M3G_UAT_COUNT] __ro_after_init;
static bool committed __ro_after_init;
static unsigned int requests __initdata;
static bool argument_valid __initdata = true;

static int __init m3g_request(char *value)
{
	requests++;
	argument_valid &= value && !strcmp(value, "1");
	return 0;
}
early_param("m3max_gpu_linear_memory", m3g_request);

static bool __init valid(struct m3g_range r)
{
	return r.base && r.bytes && r.base <= U64_MAX - r.bytes;
}

static bool __init overlaps(struct m3g_range a, struct m3g_range b)
{
	return a.base < b.base + b.bytes && b.base < a.base + a.bytes;
}

static u64 __init cell64(const void *p)
{
	return (u64)fdt32_to_cpu(((const fdt32_t *)p)[0]) << 32 |
		fdt32_to_cpu(((const fdt32_t *)p)[1]);
}

static bool __init string(const void *dt, int node, const char *key, const char *wanted)
{
	int len;
	const char *p = fdt_getprop(dt, node, key, &len);

	return p && len == strlen(wanted) + 1 && !memcmp(p, wanted, len);
}

static bool __init enabled(const void *dt, int node)
{
	int len;
	const void *p = fdt_getprop(dt, node, "status", &len);

	return (!p && len == -FDT_ERR_NOTFOUND) || string(dt, node, "status", "okay") ||
		string(dt, node, "status", "ok");
}

static bool __init unique_handles(const void *dt)
{
	int node, depth = 0, len;
	unsigned int count = 0;

	for (node = fdt_next_node(dt, -1, &depth); node >= 0 && depth >= 0;
	     node = fdt_next_node(dt, node, &depth)) {
		const char * const keys[] = { "phandle", "linux,phandle" };
		u32 handle = 0;
		unsigned int i;

		if (++count > 4096)
			return false;
		for (i = 0; i < ARRAY_SIZE(keys); i++) {
			const fdt32_t *p = fdt_getprop(dt, node, keys[i], &len);

			if (!p && len == -FDT_ERR_NOTFOUND)
				continue;
			if (!p || len != 4 || !fdt32_to_cpu(*p) || fdt32_to_cpu(*p) == U32_MAX ||
			    (handle && handle != fdt32_to_cpu(*p)) ||
			    fdt_node_offset_by_phandle(dt, fdt32_to_cpu(*p)) != node)
				return false;
			handle = fdt32_to_cpu(*p);
		}
	}
	return count != 0;
}

static bool __init map_valid(void)
{
	efi_memory_desc_t *md, *other;
	u64 bytes, end, other_bytes, other_end;

	if (!efi_enabled(EFI_BOOT) || !efi.memmap.map || efi.memmap.nr_map <= 0 ||
	    efi.memmap.nr_map > 1024 || efi.memmap.desc_version != 1 ||
	    efi.memmap.desc_size < sizeof(*md) || efi.memmap.desc_size > 256 ||
	    (efi.memmap.desc_size & 7))
		return false;
	for_each_efi_memory_desc(md) {
		if (!md->num_pages || (md->phys_addr & 4095) ||
		    check_mul_overflow(md->num_pages, 4096ULL, &bytes) ||
		    check_add_overflow(md->phys_addr, bytes, &end))
			return false;
		for_each_efi_memory_desc(other) {
			if (other == md)
				break;
			if (check_mul_overflow(other->num_pages, 4096ULL, &other_bytes) ||
			    check_add_overflow(other->phys_addr, other_bytes, &other_end) ||
			    (md->phys_addr < other_end && other->phys_addr < end))
				return false;
		}
	}
	return true;
}

/* Only reserved or genuinely unlisted memory outside original DT RAM can
 * change ownership here. Runtime/allocated/free EFI memory never qualifies.
 */
static bool __init efi_region(struct m3g_range r, unsigned int role)
{
	u64 at = r.base, end = at + r.bytes;

	while (at < end) {
		efi_memory_desc_t *md;
		u64 next = end;

		for_each_efi_memory_desc(md) {
			u64 last = md->phys_addr + (md->num_pages << EFI_PAGE_SHIFT);

			if (at >= md->phys_addr && at < last) {
				u64 cache = md->attribute & 31;

				if (md->type != EFI_RESERVED_TYPE ||
				    (md->attribute & (EFI_MEMORY_RUNTIME | EFI_MEMORY_RP |
						 EFI_MEMORY_SP | EFI_MEMORY_HOT_PLUGGABLE)) ||
				    (role < M3G_UAT_COUNT && cache && cache != EFI_MEMORY_WB) ||
				    (role != M3G_UAT_COUNT && (md->attribute & (EFI_MEMORY_RO | EFI_MEMORY_WP))))
					return false;
				next = min(end, last);
				break;
			}
			if (md->phys_addr > at)
				next = min(next, md->phys_addr);
		}
		at = next;
	}
	return true;
}

static bool __init dt_banks(const void *dt, const struct m3g_range *ranges,
			   struct m3g_range dram)
{
	int node = fdt_path_offset(dt, "/memory"), len, i, j;
	const fdt32_t *reg;

	if (node < 0 || !enabled(dt, node) || !string(dt, node, "device_type", "memory") ||
	    fdt_node_offset_by_prop_value(dt, -1, "device_type", "memory", 7) != node ||
	    fdt_node_offset_by_prop_value(dt, node, "device_type", "memory", 7) != -FDT_ERR_NOTFOUND)
		return false;
	reg = fdt_getprop(dt, node, "reg", &len);
	if (!reg || len <= 0 || len % 16 || len > 16 * 16)
		return false;
	for (i = 0; i < len / 16; i++) {
		struct m3g_range bank = { cell64(reg + 4 * i), cell64(reg + 4 * i + 2) };

		if (!valid(bank) || ((bank.base | bank.bytes) & 0x3fff) ||
		    bank.base < dram.base || bank.bytes > dram.bytes ||
		    bank.base - dram.base > dram.bytes - bank.bytes)
			return false;
		for (j = 0; j < M3G_REGION_COUNT; j++)
			if (overlaps(bank, ranges[j]))
				return false;
		for (j = 0; j < i; j++) {
			struct m3g_range prior = { cell64(reg + 4 * j), cell64(reg + 4 * j + 2) };

			if (overlaps(bank, prior))
				return false;
		}
	}
	return true;
}

static bool __init dt_owners(const void *dt, const struct m3g_range *ranges, int *selected)
{
	int gpu = fdt_path_offset(dt, "gpu"), parent = fdt_path_offset(dt, "/reserved-memory");
	int len, i, j, node;
	const fdt32_t *refs, *vas, *flags;
	const void *empty;

	if (gpu < 0 || fdt_node_check_compatible(dt, gpu, "apple,agx-t6034") ||
	    !(enabled(dt, gpu) || string(dt, gpu, "status", "disabled")) || parent < 0 ||
	    fdt_address_cells(dt, parent) != 2 || fdt_size_cells(dt, parent) != 2)
		return false;
	empty = fdt_getprop(dt, parent, "ranges", &len);
	if (!empty || len || fdt_stringlist_count(dt, gpu, "memory-region-names") != M3G_REGION_COUNT)
		return false;
	refs = fdt_getprop(dt, gpu, "memory-region", &len);
	if (!refs || len != 4 * M3G_REGION_COUNT)
		return false;
	for (i = 0; i < M3G_REGION_COUNT; i++) {
		const fdt32_t *reg;
		int index = fdt_stringlist_search(dt, gpu, "memory-region-names", names[i]);

		if (index < 0 || index >= M3G_REGION_COUNT)
			return false;
		node = fdt_node_offset_by_phandle(dt, fdt32_to_cpu(refs[index]));
		if (node < 0 || fdt_parent_offset(dt, node) != parent || !enabled(dt, node))
			return false;
		for (j = 0; j < i; j++)
			if (node == selected[j])
				return false;
		selected[i] = node;
		if (fdt_getprop(dt, node, "reusable", &len) || len != -FDT_ERR_NOTFOUND)
			return false;
		if (i < M3G_UAT_COUNT) {
			const char *name = fdt_get_name(dt, node, NULL);
			size_t n = strlen(owners[i]);
			unsigned int count = 0;
			int child;

			if (!name || strncmp(name, owners[i], n) || (name[n] && name[n] != '@') ||
			    fdt_getprop(dt, node, "no-map", &len) || len != -FDT_ERR_NOTFOUND ||
			    fdt_getprop(dt, node, "reusable", &len) || len != -FDT_ERR_NOTFOUND)
				return false;
			fdt_for_each_subnode(child, dt, parent) {
				name = fdt_get_name(dt, child, NULL);
				if (name && !strncmp(name, owners[i], n) && (!name[n] || name[n] == '@'))
					count++;
			}
			if (count != 1)
				return false;
		} else {
			empty = fdt_getprop(dt, node, "no-map", &len);
			if (!empty || len)
				return false;
		}
		reg = fdt_getprop(dt, node, "reg", &len);
		if (!reg || len != 16 || cell64(reg) != ranges[i].base || cell64(reg + 2) != ranges[i].bytes)
			return false;
	}
	vas = fdt_getprop(dt, gpu, "apple,firmware-segment-vas", &len);
	if (!vas || len != 16 || cell64(vas) != M3G_FW_UPPER || cell64(vas + 2) != M3G_FW_UPPER + ranges[4].bytes)
		return false;
	flags = fdt_getprop(dt, gpu, "apple,firmware-segment-flags", &len);
	if (!flags || len != 8 || fdt32_to_cpu(flags[0]) != 1 || fdt32_to_cpu(flags[1]))
		return false;
	flags = fdt_getprop(dt, gpu, "apple,m3-handoff-version", &len);
	return flags && len == 4 && fdt32_to_cpu(*flags) == 1;
}

static bool __init dt_conflicts(const void *dt, const struct m3g_range *ranges, const int *selected)
{
	int parent = fdt_path_offset(dt, "/reserved-memory"), node, i, j, len, count;

	fdt_for_each_subnode(node, dt, parent) {
		const fdt32_t *reg;
		bool own = false;

		for (i = 0; i < M3G_REGION_COUNT; i++)
			own |= node == selected[i];
		if (own || string(dt, node, "status", "disabled"))
			continue;
		if (!enabled(dt, node))
			return false;
		reg = fdt_getprop(dt, node, "reg", &len);
		if (!reg || len <= 0 || len % 16 || len > 16 * 16)
			return false;
		for (i = 0; i < len / 16; i++) {
			struct m3g_range r = { cell64(reg + 4 * i), cell64(reg + 4 * i + 2) };

			if (!r.base && !r.bytes)
				continue;
			if (!valid(r))
				return false;
			for (j = 0; j < M3G_REGION_COUNT; j++)
				if (overlaps(r, ranges[j]))
					return false;
		}
	}
	count = fdt_num_mem_rsv(dt);
	if (count < 0 || count > 64)
		return false;
	for (i = 0; i < count; i++) {
		struct m3g_range r;

		if (fdt_get_mem_rsv(dt, i, &r.base, &r.bytes) || !valid(r))
			return false;
		for (j = 0; j < M3G_REGION_COUNT; j++)
			if (overlaps(r, ranges[j]))
				return false;
	}
	return true;
}

void __init m3max_gpu_linear_init(phys_addr_t memory_limit)
{
	const void *dt = initial_boot_params;
	const fdt32_t *wire;
	struct m3g_range ranges[M3G_REGION_COUNT], dram;
	int selected[M3G_REGION_COUNT], chosen, len, i, j;
	u64 span = PAGE_END - _PAGE_OFFSET(vabits_actual);

	if (!requests)
		return;
	if (requests != 1 || !argument_valid || memory_limit != PHYS_ADDR_MAX || !dt || fdt_check_header(dt) ||
	    fdt_totalsize(dt) > SZ_2M || !unique_handles(dt) || fdt_node_check_compatible(dt, 0, "apple,j514m") ||
	    fdt_node_check_compatible(dt, 0, "apple,t6034") || !map_valid())
		panic("M3 Max GPU linear memory: boot context refused");
	chosen = fdt_path_offset(dt, "/chosen");
	wire = fdt_getprop(dt, chosen, M3G_PROPERTY, &len);
	if (!wire || len != M3G_CELLS * 8 || cell64(wire + 2 * M3G_SCHEMA) != M3G_VERSION ||
	    cell64(wire + 2 * M3G_COUNT) != M3G_REGION_COUNT ||
	    cell64(wire + 2 * M3G_MAP_BASE) != efi.memmap.phys_map ||
	    cell64(wire + 2 * M3G_MAP_BYTES) != efi.memmap.nr_map * efi.memmap.desc_size ||
	    cell64(wire + 2 * M3G_MAP_STRIDE) != efi.memmap.desc_size ||
	    cell64(wire + 2 * M3G_MAP_VERSION) != efi.memmap.desc_version)
		panic("M3 Max GPU linear memory: final EFI binding refused");
	dram = (struct m3g_range){ cell64(wire + 2 * M3G_DRAM_BASE), cell64(wire + 2 * M3G_DRAM_BYTES) };
	if (!valid(dram) || (dram.base | dram.bytes) & 0x3fff)
		panic("M3 Max GPU linear memory: DRAM geometry refused");
	for (i = 0; i < M3G_REGION_COUNT; i++) {
		ranges[i] = (struct m3g_range){ cell64(wire + 2 * (M3G_RANGES + 2 * i)),
			cell64(wire + 2 * (M3G_RANGES + 2 * i + 1)) };
		if (!valid(ranges[i]) || ((ranges[i].base | ranges[i].bytes) & 0x3fff) ||
		    ranges[i].base < dram.base || ranges[i].bytes > dram.bytes ||
		    ranges[i].base - dram.base > dram.bytes - ranges[i].bytes ||
		    ranges[i].base > PHYS_MASK || ranges[i].bytes - 1 > PHYS_MASK - ranges[i].base ||
		    (i < M3G_UAT_COUNT && (ranges[i].bytes != uat_bytes[i] ||
			(ranges[i].base < memstart_addr || ranges[i].bytes > span ||
			 ranges[i].base - memstart_addr > span - ranges[i].bytes))) ||
		    (i >= M3G_UAT_COUNT && ranges[i].bytes > SZ_16M) || !efi_region(ranges[i], i))
			panic("M3 Max GPU linear memory: range refused");
		for (j = 0; j < i; j++)
			if (overlaps(ranges[i], ranges[j]))
				panic("M3 Max GPU linear memory: overlapping ranges");
	}
	if (fdt_address_cells(dt, 0) != 2 || fdt_size_cells(dt, 0) != 2 ||
	    !dt_banks(dt, ranges, dram) || !dt_owners(dt, ranges, selected) ||
	    !dt_conflicts(dt, ranges, selected))
		panic("M3 Max GPU linear memory: tree ownership refused");
	for (i = 0; i < M3G_REGION_COUNT; i++) {
		struct m3g_range kernel = { __pa_symbol(_text), _end - _text };
		struct m3g_range fdt = { __fdt_pointer, fdt_totalsize(dt) };
		struct m3g_range map = { efi.memmap.phys_map, efi.memmap.nr_map * efi.memmap.desc_size };
		struct m3g_range rd = { phys_initrd_start, phys_initrd_size };

		if (overlaps(ranges[i], kernel) || overlaps(ranges[i], fdt) || overlaps(ranges[i], map) ||
		    (rd.bytes && overlaps(ranges[i], rd)) || memblock_is_region_reserved(ranges[i].base, ranges[i].bytes))
			panic("M3 Max GPU linear memory: live owner conflict");
	}
	/* Reserve every table range before making any of it allocatable RAM. */
	for (i = 0; i < M3G_UAT_COUNT; i++)
		if (memblock_reserve(ranges[i].base, ranges[i].bytes))
			panic("M3 Max GPU linear memory: reservation failed");
	for (i = 0; i < M3G_UAT_COUNT; i++) {
		if (memblock_add(ranges[i].base, ranges[i].bytes) || memblock_clear_nomap(ranges[i].base, ranges[i].bytes))
			panic("M3 Max GPU linear memory: mapping preparation failed");
		admitted[i] = ranges[i];
	}
	committed = true;
	pr_info("M3 Max GPU linear memory: four UAT ranges reserved before paging\n");
}

static int __init m3g_verify_pages(void)
{
	unsigned int i;

	if (!committed)
		return 0;
	for (i = 0; i < M3G_UAT_COUNT; i++) {
		unsigned long pfn = PHYS_PFN(admitted[i].base);
		unsigned long end = PHYS_PFN(admitted[i].base + admitted[i].bytes);

		for (; pfn < end; pfn++)
			if (!pfn_valid(pfn) || !pfn_is_map_memory(pfn) || !PageReserved(pfn_to_page(pfn)) ||
			    !virt_addr_valid(__va(PFN_PHYS(pfn))))
				panic("M3 Max GPU linear memory: reserved linear page verification failed");
	}
	pr_info("M3 Max GPU linear memory: all UAT pages reserved and linearly mapped\n");
	return 0;
}
arch_initcall(m3g_verify_pages);

// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Experimental per-kernel preparation; never starts/powers the GPU. */
#include <linux/efi.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/libfdt.h>
#include <linux/memblock.h>
#include <linux/of_fdt.h>
#include <linux/string.h>
#include <asm/early_ioremap.h>
#include <asm/memory.h>
#include "asahi-gpu-memory.h"

/* Only staging buffers live in init memory. The published tree is copied to
 * independently reserved normal RAM before unflattening, never left here.
 */
static u8 adt_copy[AGM_MAX_BLOB] __initdata __aligned(8);
static u8 planned_fdt[AGM_MAX_BLOB] __initdata __aligned(8);
static int requested __initdata;
static phys_addr_t staged_tree __initdata;
static int staged_tree_size __initdata;
static struct agm_layout staged_layout __initdata;

static int __init request_memory(char *arg)
{
	/* Reject duplicates instead of accepting an ambiguous last option. */
	if (requested || !arg || strcmp(arg, "1")) {
		requested = -1;
		return -EINVAL;
	}
	requested = 1;
	return 0;
}
early_param("asahi_t6034_gpu_memory", request_memory);

static bool __init reserved_full(struct agm_span s)
{
	struct memblock_region *r;
	u64 cursor = s.base, end = s.base + s.size;

	for_each_reserved_mem_region(r) {
		if (r->base > cursor)
			break;
		if (r->base + r->size > cursor)
			cursor = r->base + r->size;
		if (cursor >= end)
			return true;
	}
	return false;
}

static bool __init intersects(struct agm_span s, u64 base, u64 size)
{
	return s.base < base + size && base < s.base + s.size;
}

/* Reject EVERY overlapping EFI descriptor, including reserved and non-RAM.
 * No reinterpretation of previously allocatable or unknown EFI memory.
 * Together with the inherited complete FDT memory list, this is evidence
 * about the boot chain's declared allocation domain, not its entire history.
 */
static int __init excluded(const struct agm_layout *l, u64 linear_size)
{
	efi_memory_desc_t *md;
	int i;

	if (!efi_enabled(EFI_BOOT) || !efi.memmap.map ||
	    efi.memmap.desc_size < sizeof(*md) || !efi.memmap.nr_map ||
	    efi.memmap.nr_map < 0 || (unsigned int)efi.memmap.nr_map > AGM_MAX_BLOB / sizeof(*md) ||
	    efi.memmap.desc_size > 4096 ||
	    (unsigned long)efi.memmap.map_end < (unsigned long)efi.memmap.map ||
	    (unsigned long)efi.memmap.map_end - (unsigned long)efi.memmap.map !=
		(u64)efi.memmap.nr_map * efi.memmap.desc_size ||
	    memstart_addr < 0 || (PAGE_SIZE != 4096 && PAGE_SIZE != 16384))
		return -EINVAL;
	for (i = 0; i < 4; i++) {
		struct agm_span s = l->uat[i];

		if (s.base + s.size - 1 > PHYS_MASK ||
		    memblock_overlaps_region(&memblock.memory, s.base, s.size) ||
		    memblock_overlaps_region(&memblock.reserved, s.base, s.size))
			return -EBUSY;
		if (i != 2 && (s.base < (u64)memstart_addr || s.base - memstart_addr >= linear_size ||
			       s.size > linear_size - (s.base - memstart_addr)))
			return -ERANGE;
	}
	for_each_efi_memory_desc(md) {
		u64 size;

		if (!md->num_pages || md->num_pages > (U64_MAX >> EFI_PAGE_SHIFT))
			return -EINVAL;
		size = md->num_pages << EFI_PAGE_SHIFT;
		if (md->phys_addr > U64_MAX - size)
			return -EINVAL;
		for (i = 0; i < 4; i++)
			if (intersects(l->uat[i], md->phys_addr, size))
				return -EBUSY;
	}
	return 0;
}

static int __init copy_tree(phys_addr_t dest, size_t size)
{
	size_t offset;

	for (offset = 0; offset < size; offset += PAGE_SIZE) {
		size_t bytes = min_t(size_t, PAGE_SIZE, size - offset);
		void *map = early_memremap(dest + offset, bytes);

		if (!map)
			return -ENOMEM;
		memcpy(map, planned_fdt + offset, bytes);
		early_memunmap(map, bytes);
	}
	return 0;
}

static void __init verify_ownership(const struct agm_layout *layout)
{
	u64 pa;
	int i;

	for (i = 0; i < 4; i++) {
		struct agm_span s = layout->uat[i];

		if (!reserved_full(s))
			panic("T6034 GPU memory lost reservation");
		if (i == 2) {
			if (memblock_overlaps_region(&memblock.memory, s.base, s.size))
				panic("T6034 GPU handoff acquired a linear alias");
		} else {
			if (!memblock_is_region_memory(s.base, s.size))
				panic("T6034 GPU table memory incomplete");
			for (pa = s.base; pa < s.base + s.size; pa += PAGE_SIZE)
				if (!memblock_is_map_memory(pa))
					panic("T6034 GPU table memory is not mapped");
		}
	}
}

static int __init prepare(u64 linear_size)
{
	struct agm_layout layout;
	struct agm_span source;
	phys_addr_t new_tree;
	u64 pa;
	int ret, size, i;

	if (!initial_boot_params || !efi_enabled(EFI_BOOT))
		return -EINVAL;
	size = fdt_totalsize(initial_boot_params);
	ret = agm_fdt_source(initial_boot_params, size, &source);
	if (ret)
		return ret;
	/* Only the exact enabled retained-ADT owner can authorize this metadata
	 * read, and it must actually be wholly reserved, mapped normal RAM.
	 */
	if (source.base + source.size - 1 > PHYS_MASK ||
	    !memblock_is_region_memory(source.base, source.size) || !reserved_full(source))
		return -EINVAL;
	for (pa = source.base; pa < source.base + source.size; pa += PAGE_SIZE)
		if (!memblock_is_map_memory(pa))
			return -EINVAL;
	ret = copy_from_early_mem(adt_copy, source.base, source.size);
	if (ret)
		return ret;
	ret = agm_adt_layout(adt_copy, source.size, &layout);
	if (ret)
		return ret;
	ret = excluded(&layout, linear_size);
	if (ret)
		return ret;
	ret = agm_fdt_plan(initial_boot_params, size, planned_fdt, sizeof(planned_fdt), &layout);
	if (ret)
		return ret;
	size = fdt_totalsize(planned_fdt);
	new_tree = memblock_phys_alloc(PAGE_ALIGN(size), PAGE_SIZE);
	if (!new_tree)
		return -ENOMEM;
	ret = copy_tree(new_tree, size);
	if (ret) {
		memblock_phys_free(new_tree, PAGE_ALIGN(size));
		return ret;
	}
	/* Commit: reserve before adding memory, so even memblock's own array
	 * resizing cannot allocate from a newly introduced GPU table region.
	 * A mutation failure stops this experimental boot rather than publishing
	 * partial ownership. Its next-boot selection is an external prerequisite.
	 */
	for (i = 0; i < 4; i++)
		if (memblock_reserve(layout.uat[i].base, layout.uat[i].size))
			panic("T6034 GPU memory reservation commit failed");
	for (i = 0; i < 4; i++)
		if (i != 2 && memblock_add(layout.uat[i].base, layout.uat[i].size))
			panic("T6034 GPU table memory commit failed");
	verify_ownership(&layout);
	/* The existing FDT fixmap is live: replacing its PFNs violates the
	 * ARM64 mapping guard (and would invalidate earlier FDT pointers).
	 * Keep it intact. paging_init() will map this separately reserved
	 * normal-RAM copy, which we publish before unflattening the tree.
	 */
	staged_tree = new_tree;
	staged_tree_size = size;
	staged_layout = layout;
	return 0;
}

void __init asahi_gpu_early_memory(bool unrestricted, u64 linear_size)
{
	int ret;

	if (!requested)
		return;
	ret = requested == 1 && unrestricted ? prepare(linear_size) : -EINVAL;
	if (ret)
		pr_warn("T6034 GPU memory not prepared (%d); GPU remains disabled\n", ret);
}

void __init asahi_gpu_publish_memory(void)
{
	struct agm_span source;
	void *mapped;
	u64 pa;
	int size;

	if (!staged_tree)
		return;
	source = (struct agm_span){ staged_tree, PAGE_ALIGN(staged_tree_size) };
	if (!reserved_full(source) ||
	    !memblock_is_region_memory(source.base, source.size))
		panic("T6034 GPU memory tree reservation lost");
	for (pa = source.base; pa < source.base + source.size; pa += PAGE_SIZE)
		if (!memblock_is_map_memory(pa))
			panic("T6034 GPU memory tree is not mapped");
	mapped = phys_to_virt(staged_tree);
	size = fdt_totalsize(mapped);
	if (size != staged_tree_size || !early_init_dt_verify(mapped, staged_tree))
		panic("T6034 GPU memory tree publication failed");
	/* Refresh static owner inventory/counts before unflattening. Dynamic
	 * size/alignment regions were refused before the reservation commit.
	 */
	early_init_fdt_scan_reserved_mem();
	verify_ownership(&staged_layout);
	staged_tree = 0;
	pr_info("T6034 GPU memory prepared; GPU remains disabled\n");
}

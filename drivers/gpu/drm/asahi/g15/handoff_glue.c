// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Inactive J514M/T6034 Normal-NC handoff admission. No mapping or hardware IO. */
#include <linux/device.h>
#include <linux/io.h>
#include <linux/ioport.h>
#include <linux/mm.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/overflow.h>
#include <linux/pfn.h>
#include <linux/sizes.h>
#include <linux/string.h>
#include <asm/page.h>

unsigned int asahi_g15_memory_mode(u64 start, u64 size);
bool asahi_g15_handoff_nc(struct device *dev, u64 start, u64 size);

bool asahi_g15_handoff_nc(struct device *dev, u64 start, u64 size)
{
	static const char policy[] = "no-map-normal-nc-v1";
	struct device_node *gpu, *owner = NULL, *rmem = NULL, *child;
	struct resource res;
	const void *value;
	u64 end, pa;
	int count, index = -1, length, i;
	u32 cells;
	bool accepted = false;

	if (!dev || !(gpu = dev->of_node) ||
	    !of_machine_is_compatible("apple,j514m") ||
	    !of_machine_is_compatible("apple,t6034") ||
	    !of_device_is_compatible(gpu, "apple,agx-t6034") ||
	    size != SZ_16K || !IS_ALIGNED(start, SZ_16K) ||
	    !IS_ALIGNED(start, PAGE_SIZE) || !IS_ALIGNED(size, PAGE_SIZE) ||
	    check_add_overflow(start, size, &end))
		return false;
	value = of_get_property(gpu, "asahi,g15c-handoff-memory", &length);
	if (!value || length != (int)sizeof(policy) || memcmp(value, policy, sizeof(policy)))
		return false;
	count = of_property_count_strings(gpu, "memory-region-names");
	if (count < 1 || of_count_phandle_with_args(gpu, "memory-region", NULL) != count)
		return false;
	for (i = 0; i < count; i++) {
		const char *name;

		if (of_property_read_string_index(gpu, "memory-region-names", i, &name))
			return false;
		if (!strcmp(name, "handoff")) {
			if (index >= 0)
				return false;
			index = i;
		}
	}
	if (index < 0)
		return false;
	owner = of_parse_phandle(gpu, "memory-region", index);
	rmem = of_find_node_by_path("/reserved-memory");
	if (!owner || !rmem || owner->parent != rmem ||
	    !of_device_is_available(owner) || !of_node_name_eq(owner, "uat-handoff") ||
	    of_property_read_bool(owner, "reusable") ||
	    !of_find_property(owner, "no-map", &length) || length != 0 ||
	    !of_find_property(owner, "reg", &length) || length != 16 ||
	    of_property_read_u32(rmem, "#address-cells", &cells) || cells != 2 ||
	    of_property_read_u32(rmem, "#size-cells", &cells) || cells != 2 ||
	    !of_find_property(rmem, "ranges", &length) || length != 0 ||
	    of_address_to_resource(owner, 0, &res) || res.start != start ||
	    resource_size(&res) != size)
		goto out;

	/* Another enabled reservation must not claim any part of this page.
	 * Multi-tuple static reservations are checked completely. Dynamic regions
	 * without reg cannot allocate this excluded no-map physical page.
	 */
	for_each_available_child_of_node(rmem, child) {
		if (child == owner)
			continue;
		if (of_node_name_eq(child, "uat-handoff")) {
			of_node_put(child);
			goto out;
		}
		if (of_find_property(child, "reg", &length) &&
		    (length <= 0 || length % 16 || !of_get_address(child, 0, NULL, NULL))) {
			of_node_put(child);
			goto out;
		}
		for (i = 0; of_get_address(child, i, NULL, NULL); i++) {
			if (of_address_to_resource(child, i, &res) ||
			    (res.start < end && start <= res.end)) {
				of_node_put(child);
				goto out;
			}
		}
	}

	/* A no-map property alone does not prove early reservation succeeded.
	 * Check every PFN plus the same classifier both UAT mapping paths use.
	 * This excludes the standard linear WB alias, not arbitrary aliases
	 * another kernel component could deliberately create.
	 */
	if (asahi_g15_memory_mode(start, size) != MEMREMAP_WC)
		goto out;
	for (pa = start; pa < end; pa += PAGE_SIZE)
		if (pfn_is_map_memory(PHYS_PFN(pa)))
			goto out;
	accepted = true;
out:
	of_node_put(rmem);
	of_node_put(owner);
	return accepted;
}

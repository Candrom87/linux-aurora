// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Same-boot metadata preparation for a separately selected kernel. No MMIO. */
#include <linux/errno.h>
#include <linux/libfdt.h>
#include <linux/limits.h>
#include <linux/string.h>
#include <linux/unaligned.h>
#include "asahi-gpu-memory.h"

static const char * const region_names[] = { "ttbs", "pagetables", "handoff", "pagetables-l2" };
static const char * const owner_names[] = { "uat-ttbs", "uat-pagetables", "uat-handoff", "uat-pagetables-l2" };
static const char * const adt_names[] = { "gpu-region", "gfx-shared-region", "gfx-handoff", "gfx-shared-l2-region" };
static const u64 region_sizes[] = { 0x4000, 0x40000, 0x4000, 0x4000 };

static bool valid(struct agm_span s)
{
	return s.size && s.base <= U64_MAX - s.size && !((s.base | s.size) & 0x3fff);
}

static bool overlap(struct agm_span a, struct agm_span b)
{
	return a.base < b.base + b.size && b.base < a.base + a.size;
}

static bool layout_valid(const struct agm_layout *l)
{
	int i, j;

	if (!valid(l->dram))
		return false;
	for (i = 0; i < 4; i++) {
		if (!valid(l->uat[i]) || l->uat[i].size != region_sizes[i] ||
		    l->uat[i].base < l->dram.base ||
		    l->uat[i].base + l->uat[i].size > l->dram.base + l->dram.size)
			return false;
		for (j = 0; j < i; j++)
			if (overlap(l->uat[i], l->uat[j]))
				return false;
	}
	return true;
}

struct adt_view { const u8 *data; size_t size; };

/* Bounded full-tree walk before any lookup. Count/extent arithmetic cannot
 * escape the supplied copy, even with hostile counts or flagged lengths.
 */
static int adt_end(struct adt_view v, size_t at, unsigned int depth, size_t *end)
{
	u32 props, children, i;

	if (depth > 32 || at > v.size || v.size - at < 8)
		return -EINVAL;
	props = get_unaligned_le32(v.data + at);
	children = get_unaligned_le32(v.data + at + 4);
	at += 8;
	if (props > (v.size - at) / 36)
		return -EINVAL;
	for (i = 0; i < props; i++) {
		size_t len, padded;

		if (v.size - at < 36)
			return -EINVAL;
		len = get_unaligned_le32(v.data + at + 32) & 0x7fffffff;
		padded = (len + 3) & ~(size_t)3;
		at += 36;
		if (padded > v.size - at)
			return -EINVAL;
		at += padded;
	}
	if (children > (v.size - at) / 8)
		return -EINVAL;
	for (i = 0; i < children; i++)
		if (adt_end(v, at, depth + 1, &at))
			return -EINVAL;
	*end = at;
	return 0;
}

/* Exact padded property key and unique occurrence; no legacy prefix lookup. */
static const u8 *adt_prop(struct adt_view v, size_t at, const char *key, size_t *length)
{
	u8 expected[32] = { 0 };
	const u8 *found = NULL;
	u32 i, count = get_unaligned_le32(v.data + at);

	if (strlen(key) >= sizeof(expected))
		return NULL;
	memcpy(expected, key, strlen(key));
	at += 8;
	for (i = 0; i < count; i++) {
		size_t len = get_unaligned_le32(v.data + at + 32) & 0x7fffffff;

		if (!memcmp(v.data + at, expected, sizeof(expected))) {
			if (found)
				return NULL;
			found = v.data + at + 36;
			*length = len;
		}
		at += 36 + ((len + 3) & ~(size_t)3);
	}
	return found;
}

static int adt_child(struct adt_view v, size_t parent, const char *name, size_t *out)
{
	size_t at = parent + 8, found = 0;
	u32 i, props = get_unaligned_le32(v.data + parent);
	u32 children = get_unaligned_le32(v.data + parent + 4);

	for (i = 0; i < props; i++) {
		size_t len = get_unaligned_le32(v.data + at + 32) & 0x7fffffff;

		at += 36 + ((len + 3) & ~(size_t)3);
	}
	for (i = 0; i < children; i++) {
		size_t length = 0;
		const u8 *p = adt_prop(v, at, "name", &length);

		if (!p || !length || memchr(p, 0, length) != p + length - 1)
			return -EINVAL;
		if (p && length == strlen(name) + 1 && !memcmp(p, name, length)) {
			if (found)
				return -EINVAL;
			found = at;
		}
		if (adt_end(v, at, 0, &at))
			return -EINVAL;
	}
	if (!found)
		return -EINVAL;
	*out = found;
	return 0;
}

static int adt_u64(struct adt_view v, size_t node, const char *name, u64 *out)
{
	size_t length = 0;
	const u8 *p = adt_prop(v, node, name, &length);

	if (!p || length != 8)
		return -EINVAL;
	*out = get_unaligned_le64(p);
	return 0;
}

int agm_adt_layout(const void *blob, size_t size, struct agm_layout *out)
{
	struct adt_view v = { blob, size };
	struct agm_layout result = { 0 };
	size_t end, chosen, armio, sgx, length = 0;
	const u8 *p;
	char key[32];
	int i;

	if (!blob || !out || size < 8 || size > AGM_MAX_BLOB || adt_end(v, 0, 0, &end))
		return -EINVAL;
	p = adt_prop(v, 0, "target-type", &length);
	if (!p || length != sizeof("J514m") || memcmp(p, "J514m", length) ||
	    adt_child(v, 0, "chosen", &chosen) || adt_child(v, 0, "arm-io", &armio) ||
	    adt_child(v, armio, "sgx", &sgx))
		return -EINVAL;
	p = adt_prop(v, sgx, "compatible", &length);
	if (!p || length != sizeof("gpu,t6034") || memcmp(p, "gpu,t6034", length) ||
	    adt_u64(v, chosen, "dram-base", &result.dram.base) ||
	    adt_u64(v, chosen, "dram-size", &result.dram.size))
		return -EINVAL;
	for (i = 0; i < 4; i++) {
		/* All static names plus suffix fit the fixed local array. */
		strcpy(key, adt_names[i]);
		strcat(key, "-base");
		if (adt_u64(v, sgx, key, &result.uat[i].base))
			return -EINVAL;
		strcpy(key, adt_names[i]);
		strcat(key, "-size");
		if (adt_u64(v, sgx, key, &result.uat[i].size))
			return -EINVAL;
	}
	if (!layout_valid(&result))
		return -EINVAL;
	*out = result;
	return 0;
}

static bool prop_is(const void *fdt, int node, const char *name, const char *expected)
{
	int len;
	const char *p = fdt_getprop(fdt, node, name, &len);

	return p && len >= 0 && (size_t)len == strlen(expected) + 1 && !memcmp(p, expected, len);
}

static bool absent(const void *fdt, int node, const char *name)
{
	int len;

	return !fdt_getprop(fdt, node, name, &len) && len == -FDT_ERR_NOTFOUND;
}

static bool enabled(const void *fdt, int node)
{
	return absent(fdt, node, "status") || prop_is(fdt, node, "status", "okay") ||
	       prop_is(fdt, node, "status", "ok");
}

static bool basename_is(const void *fdt, int node, const char *name)
{
	int len;
	const char *p = fdt_get_name(fdt, node, &len);
	size_t n = strlen(name);

	return p && len >= 0 && (size_t)len >= n && !memcmp(p, name, n) &&
	       ((size_t)len == n || p[n] == '@');
}

static int unique_child(const void *fdt, int parent, const char *name)
{
	int node, found = -1;

	fdt_for_each_subnode(node, fdt, parent) {
		if (basename_is(fdt, node, name)) {
			if (found >= 0)
				return -EINVAL;
			found = node;
		}
	}
	return found;
}

static bool cells_two(const void *fdt, int node)
{
	int len;
	const fdt32_t *p = fdt_getprop(fdt, node, "#address-cells", &len);

	if (!p || len != 4 || fdt32_ld(p) != 2)
		return false;
	p = fdt_getprop(fdt, node, "#size-cells", &len);
	return p && len == 4 && fdt32_ld(p) == 2;
}

static int envelope(const void *fdt, size_t size)
{
	int rmem, aliases, gpu, len, node, found = -1;
	char canonical[512];
	const char *path;

	if (!fdt || (unsigned long)fdt % 8 || size < 40 || size > AGM_MAX_BLOB ||
	    fdt_check_full(fdt, size) || !cells_two(fdt, 0) ||
	    fdt_node_check_compatible(fdt, 0, "apple,j514m") ||
	    fdt_node_check_compatible(fdt, 0, "apple,t6034"))
		return -EINVAL;
	rmem = unique_child(fdt, 0, "reserved-memory");
	aliases = unique_child(fdt, 0, "aliases");
	if (rmem < 0 || aliases < 0 || !cells_two(fdt, rmem) || !enabled(fdt, rmem))
		return -EINVAL;
	if (strcmp(fdt_get_name(fdt, rmem, NULL), "reserved-memory") ||
	    strcmp(fdt_get_name(fdt, aliases, NULL), "aliases"))
		return -EINVAL;
	path = fdt_getprop(fdt, rmem, "ranges", &len);
	if (!path || len)
		return -EINVAL;
	path = fdt_getprop(fdt, aliases, "gpu", &len);
	if (!path || len < 2 || path[0] != '/' || path[len - 1] ||
	    strlen(path) != (size_t)len - 1)
		return -EINVAL;
	gpu = fdt_path_offset(fdt, path);
	if (gpu <= 0 || fdt_node_check_compatible(fdt, gpu, "apple,agx-t6034") ||
	    !prop_is(fdt, gpu, "status", "disabled") ||
	    fdt_get_path(fdt, gpu, canonical, sizeof(canonical)) || strcmp(path, canonical))
		return -EINVAL;
	/* A canonical path string can still name duplicate sibling nodes.
	 * Require every literal component on this path to be unique as well.
	 */
	for (node = gpu; node > 0; node = fdt_parent_offset(fdt, node)) {
		int parent = fdt_parent_offset(fdt, node), child, matches = 0;
		const char *name = fdt_get_name(fdt, node, NULL);

		if (parent < 0 || !name)
			return -EINVAL;
		fdt_for_each_subnode(child, fdt, parent)
			if (!strcmp(fdt_get_name(fdt, child, NULL), name))
				matches++;
		if (matches != 1)
			return -EINVAL;
	}
	/* Do not allow a second compatible GPU hidden behind another alias. */
	node = -1;
	while ((node = fdt_next_node(fdt, node, NULL)) >= 0)
		if (!fdt_node_check_compatible(fdt, node, "apple,agx-t6034")) {
			if (found >= 0)
				return -EINVAL;
			found = node;
		}
	return found == gpu ? gpu : -EINVAL;
}

int agm_fdt_source(const void *fdt, size_t size, struct agm_span *out)
{
	struct agm_span result;
	int node, len, found = -1, rmem;
	const fdt64_t *p;

	if (!out || envelope(fdt, size) < 0)
		return -EINVAL;
	rmem = unique_child(fdt, 0, "reserved-memory");
	fdt_for_each_subnode(node, fdt, rmem) {
		if (!prop_is(fdt, node, "label", "adt"))
			continue;
		if (found >= 0 || !enabled(fdt, node) ||
		    fdt_node_check_compatible(fdt, node, "phram") ||
		    !absent(fdt, node, "no-map") || !absent(fdt, node, "reusable"))
			return -EINVAL;
		found = node;
	}
	if (found < 0)
		return -EINVAL;
	p = fdt_getprop(fdt, found, "reg", &len);
	if (!p || len != 16)
		return -EINVAL;
	result = (struct agm_span){ fdt64_ld(p), fdt64_ld(p + 1) };
	if (!valid(result) || result.size > AGM_MAX_BLOB)
		return -EINVAL;
	*out = result;
	return 0;
}

static bool disjoint_reg(const void *fdt, int node, const struct agm_layout *l)
{
	int len, at, i;
	const fdt64_t *p = fdt_getprop(fdt, node, "reg", &len);

	if (!p || len <= 0 || len % 16)
		return false;
	for (at = 0; at < len / 8; at += 2) {
		struct agm_span s = { fdt64_ld(p + at), fdt64_ld(p + at + 1) };

		if (!s.size || s.base > U64_MAX - s.size)
			return false;
		for (i = 0; i < 4; i++)
			if (overlap(s, l->uat[i]))
				return false;
	}
	return true;
}

int agm_fdt_plan(const void *fdt, size_t size, void *scratch, size_t capacity,
		 const struct agm_layout *l)
{
	int gpu, rmem, memory, node, i, j, len, names, owners[4], slot;
	const fdt32_t *phandles;
	const fdt64_t *reg;

	if (!l || !layout_valid(l) || !scratch || capacity > AGM_MAX_BLOB ||
	    capacity < size || (unsigned long)scratch % 8 ||
	    (unsigned long)fdt > ULONG_MAX - size ||
	    (unsigned long)scratch > ULONG_MAX - capacity ||
	    ((unsigned long)fdt < (unsigned long)scratch + capacity &&
	     (unsigned long)scratch < (unsigned long)fdt + size))
		return -EINVAL;
	gpu = envelope(fdt, size);
	if (gpu < 0 || !absent(fdt, gpu, "asahi,g15c-handoff-memory"))
		return -EINVAL;
	rmem = unique_child(fdt, 0, "reserved-memory");
	memory = unique_child(fdt, 0, "memory");
	if (memory < 0 || !enabled(fdt, memory) ||
	    !prop_is(fdt, memory, "device_type", "memory") ||
	    !absent(fdt, memory, "linux,usable-memory") || !disjoint_reg(fdt, memory, l))
		return -EINVAL;
	fdt_for_each_subnode(node, fdt, 0) {
		if (node != memory && !prop_is(fdt, node, "status", "disabled") &&
		    prop_is(fdt, node, "device_type", "memory"))
			return -EINVAL;
		if (basename_is(fdt, node, "chosen") &&
		    !absent(fdt, node, "linux,usable-memory-range"))
			return -EINVAL;
	}
	names = fdt_stringlist_count(fdt, gpu, "memory-region-names");
	phandles = fdt_getprop(fdt, gpu, "memory-region", &len);
	if (names < 4 || !phandles || len != names * 4)
		return -EINVAL;
	for (i = 0; i < 4; i++) {
		u32 handle;
		int found = -1;

		slot = -1;
		for (j = 0; j < names; j++) {
			const char *p = fdt_stringlist_get(fdt, gpu, "memory-region-names", j, &len);

			if (p && len >= 0 && (size_t)len == strlen(region_names[i]) && !memcmp(p, region_names[i], len)) {
				if (slot >= 0)
					return -EINVAL;
				slot = j;
			}
		}
		if (slot < 0)
			return -EINVAL;
		handle = fdt32_ld(phandles + slot);
		if (!handle || handle == U32_MAX)
			return -EINVAL;
		node = -1;
		while ((node = fdt_next_node(fdt, node, NULL)) >= 0)
			if (fdt_get_phandle(fdt, node) == handle) {
				if (found >= 0)
					return -EINVAL;
				found = node;
			}
		if (found < 0 || found != unique_child(fdt, rmem, owner_names[i]) ||
		    !prop_is(fdt, found, "status", "disabled") ||
		    !absent(fdt, found, "no-map") || !absent(fdt, found, "reusable") ||
		    !absent(fdt, found, "size") || !absent(fdt, found, "alloc-ranges"))
			return -EINVAL;
		reg = fdt_getprop(fdt, found, "reg", &len);
		if (!reg || len != 16 || fdt64_ld(reg) != 0x10ULL * (i + 1) || fdt64_ld(reg + 1))
			return -EINVAL;
		owners[i] = found;
	}
	fdt_for_each_subnode(node, fdt, rmem) {
		if (prop_is(fdt, node, "status", "disabled"))
			continue;
		if (!enabled(fdt, node) ||
		    !absent(fdt, node, "size") ||
		    (!absent(fdt, node, "reg") && !disjoint_reg(fdt, node, l)) ||
		    !absent(fdt, node, "alignment"))
			return -EINVAL;
	}
	for (j = 0; j < fdt_num_mem_rsv(fdt); j++) {
		struct agm_span s;

		if (fdt_get_mem_rsv(fdt, j, &s.base, &s.size) || !s.size || s.base > U64_MAX - s.size)
			return -EINVAL;
		for (i = 0; i < 4; i++)
			if (overlap(s, l->uat[i]))
				return -EINVAL;
	}
	if (fdt_open_into(fdt, scratch, capacity))
		return -ENOMEM;
	for (i = 0; i < 4; i++) {
		fdt64_t tuple[] = { cpu_to_fdt64(l->uat[i].base), cpu_to_fdt64(l->uat[i].size) };

		/* Earlier edits shift offsets. Resolve from the unchanged phandle. */
		node = fdt_node_offset_by_phandle(scratch, fdt_get_phandle(fdt, owners[i]));
		if (node < 0 || fdt_setprop(scratch, node, "reg", tuple, sizeof(tuple)) ||
		    fdt_setprop_string(scratch, node, "status", "okay"))
			return -ENOMEM;
		node = fdt_node_offset_by_phandle(scratch, fdt_get_phandle(fdt, owners[i]));
		if (i == 2) {
			if (fdt_setprop_empty(scratch, node, "no-map"))
				return -ENOMEM;
		} else {
			memory = unique_child(scratch, 0, "memory");
			if (fdt_appendprop(scratch, memory, "reg", tuple, sizeof(tuple)))
				return -ENOMEM;
		}
	}
	gpu = fdt_node_offset_by_compatible(scratch, -1, "apple,agx-t6034");
	if (gpu < 0 || fdt_setprop_string(scratch, gpu, "asahi,g15c-handoff-memory", "no-map-normal-nc-v1") ||
	    fdt_pack(scratch) || fdt_check_full(scratch, capacity))
		return -ENOMEM;
	return 0;
}

/* SPDX-License-Identifier: MIT */
/* Production metadata helper with modeled OF/PFN services; never maps RAM. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define EINVAL 22
#define ENODEV 19
typedef uint64_t u64;
struct device_node {
    bool available, phram, reusable, nomap, second, bad_resource;
    char label[8]; int label_size, refs;
    u64 start, size;
};
struct resource { u64 start, size; };
static struct device_node root, nodes[2];
static unsigned int children, ram_calls;
static bool root_present, ram_ok;
static struct device_node *of_node_get(struct device_node *n) { if (n) n->refs++; return n; }
static void of_node_put(struct device_node *n) { if (n) { assert(n->refs > 0); n->refs--; } }
static struct device_node *of_find_node_by_path(const char *path) {
    assert(!strcmp(path, "/reserved-memory")); return root_present ? of_node_get(&root) : NULL;
}
static bool of_device_is_available(struct device_node *n) { assert(n); return n->available; }
static struct device_node *next_child(struct device_node *r, struct device_node *prev) {
    assert(r == &root); unsigned int i = prev ? (unsigned int)(prev - nodes) + 1 : 0;
    of_node_put(prev); return i < children ? of_node_get(&nodes[i]) : NULL;
}
#define for_each_child_of_node(r, n) for ((n) = next_child((r), NULL); (n); (n) = next_child((r), (n)))
static const void *of_get_property(struct device_node *n, const char *name, int *len) {
    assert(!strcmp(name, "label")); *len = n->label_size; return n->label_size ? n->label : NULL;
}
static bool of_device_is_compatible(struct device_node *n, const char *name) {
    assert(!strcmp(name, "phram")); return n->phram;
}
static bool of_property_read_bool(struct device_node *n, const char *name) {
    if (!strcmp(name, "reusable")) return n->reusable;
    assert(!strcmp(name, "no-map")); return n->nomap;
}
static const void *of_get_address(struct device_node *n, unsigned int i, void *a, void *b) {
    assert(i == 1 && !a && !b); return n->second ? n : NULL;
}
static int of_address_to_resource(struct device_node *n, unsigned int i, struct resource *r) {
    assert(!i); r->start = n->start; r->size = n->size; return n->bad_resource ? -EINVAL : 0;
}
static u64 resource_size(struct resource *r) { return r->size; }
#define check_add_overflow(a, b, c) __builtin_add_overflow((a), (b), (c))
static bool asahi_g15_ram_within(u64 start, u64 size) {
    assert(start == nodes[0].start && size == nodes[0].size); ram_calls++; return ram_ok;
}

/* OWNER_FUNCTION */

int main(void) {
    unsigned int cases = 0;
    for (unsigned int flags = 0; flags < 1024; flags++) {
        memset(&root, 0, sizeof(root)); memset(nodes, 0, sizeof(nodes));
        root_present = !(flags & 1); root.available = !(flags & 2);
        children = flags & 4 ? 2 : 1;
        nodes[0] = (struct device_node) { .available = !(flags & 8), .phram = true,
            .reusable = !!(flags & 16), .nomap = !!(flags & 32), .second = !!(flags & 64),
            .bad_resource = !!(flags & 128), .label = "adt", .label_size = 4,
            .start = 0x100000000, .size = flags & 256 ? 0 : 0x4000 };
        nodes[1] = nodes[0]; ram_ok = !(flags & 512); ram_calls = 0;
        u64 start = 0xa5a5a5a5, size = start;
        int ret = asahi_g15_native_adt_phram(&start, &size);
        assert((ret == 0) == (flags == 0));
        if (!ret) assert(start == nodes[0].start && size == nodes[0].size);
        else assert(start == 0xa5a5a5a5 && size == start);
        assert(!root.refs && !nodes[0].refs && !nodes[1].refs);
        if (flags & 511) assert(!ram_calls);
        cases++;
    }
    for (unsigned int trial = 0; trial < 9; trial++) {
        root_present = root.available = true; children = 1; ram_ok = true; ram_calls = 0;
        nodes[0] = (struct device_node) { .available = true, .phram = true,
            .label = "adt", .label_size = 4, .start = 0x100000000, .size = 0x4000 };
        switch (trial) {
        case 0: nodes[0].size = 0x200000; break;
        case 1: nodes[0].size = 0x204000; break;
        case 2: nodes[0].size--; break;
        case 3: nodes[0].start++; break;
        case 4: nodes[0].start = UINT64_MAX - 0x3fff; break;
        case 5: nodes[0].label_size = 8; break;
        case 6: nodes[0].label[3] = 'x'; break;
        case 7: nodes[0].phram = false; break;
        default: children = 0; break;
        }
        u64 start = 0xa5a5a5a5, size = start;
        int ret = asahi_g15_native_adt_phram(&start, &size);
        assert((ret == 0) == (trial == 0));
        if (ret) assert(start == 0xa5a5a5a5 && size == start);
        assert(!root.refs && !nodes[0].refs && !nodes[1].refs);
        cases++;
    }
    assert(asahi_g15_native_adt_phram(NULL, NULL) == -EINVAL);
    printf("%u metadata cases; balanced OF references; atomic outputs\n", cases + 1);
}

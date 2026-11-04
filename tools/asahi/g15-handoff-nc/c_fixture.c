/* SPDX-License-Identifier: MIT */
/* Original OF/resource/PFN simulation. The tested helper is extracted unchanged. */
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef PAGE_SHIFT
#error PAGE_SHIFT must be selected explicitly
#endif
#define PAGE_SIZE (UINT64_C(1) << PAGE_SHIFT)
#define SZ_16K UINT64_C(16384)
#define PHYS_PFN(pa) ((pa) >> PAGE_SHIFT)
#define IS_ALIGNED(value, alignment) (!((value) & ((alignment) - 1)))
#define check_add_overflow(a, b, result) __builtin_add_overflow((a), (b), (result))
#define MEMREMAP_WB 1U
#define MEMREMAP_WC 4U
#define MAX_PROPS 12
#define MAX_NODES 7
#define MAX_CHILDREN 4
#define DATA_BYTES 128

typedef uint64_t u64;
typedef uint32_t u32;
struct property { const char *name; unsigned char data[DATA_BYTES]; int length; };
struct device_node {
    const char *name;
    struct device_node *parent;
    struct property props[MAX_PROPS];
    int nprops;
    struct device_node *children[MAX_CHILDREN];
    int nchildren, refs;
    bool resource_failure;
    u32 phandle;
};
struct device { struct device_node *of_node; };
struct resource { u64 start, end; };

static struct device_node nodes[MAX_NODES];
static struct device_node *rmem_path;
static struct device test_device;
static struct device *device_argument;
static bool root_board, root_chip;
static unsigned int memory_mode, classifier_calls, pfn_calls, bad_puts;
static int mapped_pfn;
static u64 call_start, call_size;
static unsigned int cases;
#define GPU (&nodes[0])
#define RMEM (&nodes[1])
#define OWNER (&nodes[2])
#define OTHER (&nodes[3])
#define SECOND (&nodes[4])
#define WRONG_PARENT (&nodes[5])

static void require(bool condition, const char *message)
{
    if (!condition) { fprintf(stderr, "fixture failure: %s\n", message); exit(1); }
}

static void be32(unsigned char *p, u32 word)
{
    p[0] = (unsigned char)(word >> 24); p[1] = (unsigned char)(word >> 16);
    p[2] = (unsigned char)(word >> 8); p[3] = (unsigned char)word;
}

static u32 read_be32(const unsigned char *p)
{
    return (u32)p[0] << 24 | (u32)p[1] << 16 | (u32)p[2] << 8 | p[3];
}

static void be64(unsigned char *p, u64 word)
{
    be32(p, (u32)(word >> 32)); be32(p + 4, (u32)word);
}

static u64 read_be64(const unsigned char *p)
{
    return (u64)read_be32(p) << 32 | read_be32(p + 4);
}

static struct property *property(struct device_node *node, const char *name)
{
    if (!node) return NULL;
    for (int i = 0; i < node->nprops; i++)
        if (!strcmp(node->props[i].name, name)) return &node->props[i];
    return NULL;
}

static void setprop(struct device_node *node, const char *name, const void *data, int length)
{
    struct property *p = property(node, name);
    require(length >= 0 && length <= DATA_BYTES, "property fixture capacity");
    if (!p) { require(node->nprops < MAX_PROPS, "property count capacity"); p = &node->props[node->nprops++]; }
    p->name = name; p->length = length;
    if (length) memcpy(p->data, data, (size_t)length);
}

static void removeprop(struct device_node *node, const char *name)
{
    for (int i = 0; i < node->nprops; i++)
        if (!strcmp(node->props[i].name, name)) {
            memmove(&node->props[i], &node->props[i + 1],
                    (size_t)(node->nprops - i - 1) * sizeof(node->props[0]));
            node->nprops--; return;
        }
}

static void setu32(struct device_node *node, const char *name, u32 value)
{
    unsigned char b[4]; be32(b, value); setprop(node, name, b, sizeof(b));
}

static void region(struct device_node *node, u64 start, u64 size)
{
    unsigned char b[16]; be64(b, start); be64(b + 8, size);
    setprop(node, "reg", b, sizeof(b));
}

static struct device_node *of_node_get(struct device_node *node)
{
    if (node) node->refs++;
    return node;
}

static void of_node_put(struct device_node *node)
{
    if (node) { if (node->refs <= 0) bad_puts++; else node->refs--; }
}

static bool of_machine_is_compatible(const char *name)
{
    if (!strcmp(name, "apple,j514m")) return root_board;
    if (!strcmp(name, "apple,t6034")) return root_chip;
    return false;
}

static const void *of_get_property(struct device_node *node, const char *name, int *length)
{
    struct property *p = property(node, name);
    if (length) *length = p ? p->length : -1;
    return p ? p->data : NULL;
}

static struct property *of_find_property(struct device_node *node, const char *name, int *length)
{
    struct property *p = property(node, name);
    if (length) *length = p ? p->length : -1;
    return p;
}

static int of_property_count_strings(struct device_node *node, const char *name)
{
    struct property *p = property(node, name);
    int at = 0, count = 0;
    if (!p) return -1;
    while (at < p->length) {
        const unsigned char *end = memchr(p->data + at, 0, (size_t)(p->length - at));
        if (!end) return -1;
        at = (int)(end - p->data) + 1; count++;
    }
    return count;
}

static int of_property_read_string_index(struct device_node *node, const char *name,
                                         int index, const char **out)
{
    struct property *p = property(node, name);
    int at = 0;
    if (!p || index < 0) return -1;
    for (int i = 0; at < p->length; i++) {
        const unsigned char *end = memchr(p->data + at, 0, (size_t)(p->length - at));
        if (!end) return -1;
        if (i == index) { *out = (const char *)p->data + at; return 0; }
        at = (int)(end - p->data) + 1;
    }
    return -1;
}

static bool of_device_is_compatible(struct device_node *node, const char *name)
{
    int count = of_property_count_strings(node, "compatible");
    for (int i = 0; i < count; i++) {
        const char *s;
        if (!of_property_read_string_index(node, "compatible", i, &s) && !strcmp(s, name)) return true;
    }
    return false;
}

static int of_count_phandle_with_args(struct device_node *node, const char *name,
                                      const char *cells)
{
    struct property *p = property(node, name);
    require(cells == NULL, "only fixed phandle tuples are simulated");
    if (!p || p->length % 4) return -1;
    return p->length / 4;
}

static struct device_node *of_parse_phandle(struct device_node *node, const char *name, int index)
{
    struct property *p = property(node, name);
    if (!p || index < 0 || index >= p->length / 4) return NULL;
    u32 phandle = read_be32(p->data + 4 * index);
    for (int i = 0; i < MAX_NODES; i++)
        if (phandle && nodes[i].phandle == phandle) return of_node_get(&nodes[i]);
    return NULL;
}

static struct device_node *of_find_node_by_path(const char *path)
{
    require(!strcmp(path, "/reserved-memory"), "only reserved-memory lookup expected");
    return of_node_get(rmem_path);
}

static bool of_device_is_available(struct device_node *node)
{
    struct property *p = property(node, "status");
    if (!p) return true;
    return (p->length == 5 && !memcmp(p->data, "okay", 5)) ||
           (p->length == 3 && !memcmp(p->data, "ok", 3));
}

static bool of_node_name_eq(struct device_node *node, const char *name)
{
    size_t n = strlen(name);
    return !strncmp(node->name, name, n) && (node->name[n] == 0 || node->name[n] == '@');
}

static bool of_property_read_bool(struct device_node *node, const char *name)
{
    return property(node, name) != NULL;
}

static int of_property_read_u32(struct device_node *node, const char *name, u32 *value)
{
    struct property *p = property(node, name);
    if (!p || p->length < 4) return -1;
    *value = read_be32(p->data); return 0;
}

static const void *of_get_address(struct device_node *node, int index, u64 *size, unsigned int *flags)
{
    struct property *p = property(node, "reg");
    if (!p || index < 0 || (size_t)index >= (size_t)p->length / 16) return NULL;
    const unsigned char *address = p->data + 16 * index;
    if (size) *size = read_be64(address + 8);
    if (flags) *flags = 0;
    return address;
}

static int of_address_to_resource(struct device_node *node, int index, struct resource *res)
{
    const unsigned char *p = of_get_address(node, index, NULL, NULL);
    if (!p || node->resource_failure) return -1;
    u64 start = read_be64(p), size = read_be64(p + 8);
    if (!size || start > UINT64_MAX - (size - 1)) return -1;
    res->start = start; res->end = start + size - 1; return 0;
}

static u64 resource_size(const struct resource *res)
{
    return res->end - res->start + 1;
}

static struct device_node *of_get_next_available_child(struct device_node *parent,
                                                       struct device_node *previous)
{
    int at = 0;
    if (previous) {
        while (at < parent->nchildren && parent->children[at] != previous) at++;
        require(at < parent->nchildren, "iteration child belongs to parent"); at++;
        of_node_put(previous);
    }
    for (; at < parent->nchildren; at++)
        if (of_device_is_available(parent->children[at])) return of_node_get(parent->children[at]);
    return NULL;
}
#define for_each_available_child_of_node(parent, child) \
    for ((child) = of_get_next_available_child((parent), NULL); (child); \
         (child) = of_get_next_available_child((parent), (child)))

unsigned int asahi_g15_memory_mode(u64 start, u64 size);
bool asahi_g15_handoff_nc(struct device *dev, u64 start, u64 size);
unsigned int asahi_g15_memory_mode(u64 start, u64 size)
{
    require(start == call_start && size == call_size, "classifier receives exact extent");
    classifier_calls++; return memory_mode;
}

static bool pfn_is_map_memory(u64 pfn)
{
    require(pfn >= PHYS_PFN(call_start), "PFN does not precede extent");
    u64 index = pfn - PHYS_PFN(call_start);
    require(index < SZ_16K / PAGE_SIZE, "PFN stays in extent");
    pfn_calls++; return mapped_pfn >= 0 && index == (u64)mapped_pfn;
}

#include "actual_helper.inc"

static void initialize(void)
{
    memset(nodes, 0, sizeof(nodes));
    GPU->name = "gpu"; RMEM->name = "reserved-memory";
    OWNER->name = "uat-handoff@20000000"; OTHER->name = "other@30000000";
    SECOND->name = "second@40000000"; WRONG_PARENT->name = "elsewhere";
    OWNER->phandle = 1; OTHER->phandle = 2; SECOND->phandle = 3;
    OWNER->parent = OTHER->parent = SECOND->parent = RMEM;
    RMEM->children[0] = OWNER; RMEM->nchildren = 1;
    rmem_path = RMEM; test_device.of_node = GPU; device_argument = &test_device;
    root_board = root_chip = true; memory_mode = MEMREMAP_WC;
    mapped_pfn = -1; classifier_calls = pfn_calls = bad_puts = 0;
    call_start = UINT64_C(0x20000000); call_size = SZ_16K;
    setprop(GPU, "compatible", "apple,agx-t6034", sizeof("apple,agx-t6034"));
    setprop(GPU, "asahi,g15c-handoff-memory", "no-map-normal-nc-v1", sizeof("no-map-normal-nc-v1"));
    setprop(GPU, "memory-region-names", "handoff", sizeof("handoff"));
    setu32(GPU, "memory-region", OWNER->phandle);
    setu32(RMEM, "#address-cells", 2); setu32(RMEM, "#size-cells", 2);
    setprop(RMEM, "ranges", NULL, 0); setprop(OWNER, "no-map", NULL, 0);
    region(OWNER, call_start, call_size);
}

static void check(bool expected, const char *name)
{
    bool actual = asahi_g15_handoff_nc(device_argument, call_start, call_size);
    if (actual != expected) { fprintf(stderr, "case failed: %s\n", name); exit(1); }
    require(!bad_puts, "node reference underflow");
    for (int i = 0; i < MAX_NODES; i++) require(!nodes[i].refs, "every acquired node reference released");
    if (expected) {
        require(classifier_calls == 1, "accepted extent classified exactly once");
        require(pfn_calls == SZ_16K / PAGE_SIZE, "every PFN checked on accepted path");
    }
    cases++;
}

static void add_other(struct device_node *node)
{
    require(RMEM->nchildren < MAX_CHILDREN, "child fixture capacity");
    RMEM->children[RMEM->nchildren++] = node;
}

int main(void)
{
    initialize(); check(true, "normal no-map NC admission");
    initialize(); root_board = false; check(false, "wrong machine board");
    initialize(); root_chip = false; check(false, "wrong machine chip");
    initialize(); setprop(GPU, "compatible", "apple,agx-t6031", sizeof("apple,agx-t6031")); check(false, "wrong GPU");
    initialize(); device_argument = NULL; check(false, "null device");
    initialize(); test_device.of_node = NULL; check(false, "missing OF node");
    initialize(); removeprop(GPU, "asahi,g15c-handoff-memory"); check(false, "missing marker");
    initialize(); setprop(GPU, "asahi,g15c-handoff-memory", NULL, 0); check(false, "empty marker");
    initialize(); setprop(GPU, "asahi,g15c-handoff-memory", "no-map-normal-nc-v1", sizeof("no-map-normal-nc-v1") - 1); check(false, "unterminated marker");
    initialize(); setprop(GPU, "asahi,g15c-handoff-memory", "no-map-normal-nc-v2", sizeof("no-map-normal-nc-v2")); check(false, "wrong marker");
    initialize(); removeprop(GPU, "memory-region-names"); check(false, "missing names");
    initialize(); setprop(GPU, "memory-region-names", NULL, 0); check(false, "empty names");
    initialize(); setprop(GPU, "memory-region-names", "handoff", sizeof("handoff") - 1); check(false, "malformed names");
    initialize(); setprop(GPU, "memory-region-names", "other", sizeof("other")); check(false, "no handoff name");
    initialize(); setprop(GPU, "memory-region-names", "handoff\0handoff", sizeof("handoff\0handoff"));
    { unsigned char b[8]; be32(b, 1); be32(b + 4, 2); setprop(GPU, "memory-region", b, sizeof(b)); }
    check(false, "duplicate handoff names");
    initialize(); removeprop(GPU, "memory-region"); check(false, "missing phandles");
    initialize(); setprop(GPU, "memory-region", "xx", 2); check(false, "malformed phandle cells");
    initialize(); setprop(GPU, "memory-region-names", "other\0handoff", sizeof("other\0handoff")); check(false, "name/phandle count mismatch");
    initialize(); setprop(GPU, "memory-region-names", "other\0handoff", sizeof("other\0handoff"));
    { unsigned char b[8]; be32(b, 2); be32(b + 4, 1); setprop(GPU, "memory-region", b, sizeof(b)); }
    check(true, "named handoff at second phandle");
    initialize(); setu32(GPU, "memory-region", 99); check(false, "unresolved phandle");
    initialize(); rmem_path = NULL; check(false, "missing reserved-memory parent");
    initialize(); OWNER->parent = WRONG_PARENT; check(false, "owner has wrong parent");
    initialize(); OWNER->parent = NULL; check(false, "owner has no parent");
    initialize(); OWNER->name = "something-else@20000000"; check(false, "owner has wrong name");
    initialize(); setprop(OWNER, "status", "disabled", sizeof("disabled")); check(false, "disabled owner");
    initialize(); setprop(OWNER, "status", "okay", sizeof("okay")); check(true, "explicitly available owner");
    initialize(); setprop(OWNER, "reusable", NULL, 0); check(false, "reusable owner");
    initialize(); removeprop(OWNER, "no-map"); check(false, "missing no-map");
    initialize(); setu32(OWNER, "no-map", 0); check(false, "nonempty no-map");
    initialize(); removeprop(OWNER, "reg"); check(false, "missing owner reg");
    initialize(); setprop(OWNER, "reg", NULL, 0); check(false, "empty owner reg");
    initialize(); setprop(OWNER, "reg", "invalid", 7); check(false, "malformed owner reg");
    initialize(); { unsigned char b[32] = {0}; setprop(OWNER, "reg", b, sizeof(b)); } check(false, "multiple owner tuples");
    initialize(); removeprop(RMEM, "#address-cells"); check(false, "missing address cells");
    initialize(); setu32(RMEM, "#address-cells", 1); check(false, "wrong address cells");
    initialize(); removeprop(RMEM, "#size-cells"); check(false, "missing size cells");
    initialize(); setu32(RMEM, "#size-cells", 1); check(false, "wrong size cells");
    initialize(); removeprop(RMEM, "ranges"); check(false, "missing ranges");
    initialize(); setu32(RMEM, "ranges", 0); check(false, "nonempty ranges");
    initialize(); region(OWNER, call_start + SZ_16K, call_size); check(false, "wrong owner resource address");
    initialize(); region(OWNER, call_start, call_size - 1); check(false, "wrong owner resource length");
    initialize(); OWNER->resource_failure = true; check(false, "owner resource translation failure");
    initialize(); add_other(OTHER); OTHER->name = "uat-handoff@30000000"; region(OTHER, call_start + SZ_16K * 8, SZ_16K); check(false, "enabled duplicate owner name even disjoint");
    initialize(); add_other(OTHER); OTHER->name = "uat-handoff@30000000"; region(OTHER, call_start, SZ_16K); setprop(OTHER, "status", "disabled", sizeof("disabled")); check(true, "disabled duplicate skipped");
    initialize(); add_other(OTHER); region(OTHER, call_start + 4096, 4096); check(false, "competing overlap");
    initialize(); add_other(OTHER); region(OTHER, call_start - 4096, SZ_16K + 8192); check(false, "competing containment");
    initialize(); add_other(OTHER); region(OTHER, call_start - SZ_16K, SZ_16K); check(true, "touch before is disjoint");
    initialize(); add_other(OTHER); region(OTHER, call_start + SZ_16K, SZ_16K); check(true, "touch after is disjoint");
    initialize(); add_other(OTHER);
    { unsigned char b[32]; be64(b, call_start + SZ_16K * 8); be64(b + 8, SZ_16K); be64(b + 16, call_start + 4096); be64(b + 24, 4096); setprop(OTHER, "reg", b, sizeof(b)); }
    check(false, "competing second tuple overlaps");
    initialize(); add_other(OTHER); region(OTHER, call_start + SZ_16K * 8, SZ_16K); OTHER->resource_failure = true; check(false, "competing tuple cannot translate");
    initialize(); add_other(OTHER); check(true, "dynamic competitor has no static reg");
    initialize(); add_other(OTHER); setprop(OTHER, "reg", NULL, 0); check(false, "present empty competitor reg");
    initialize(); add_other(OTHER); setprop(OTHER, "reg", "invalid", 7); check(false, "present short competitor reg");
    initialize(); add_other(OTHER);
    { unsigned char b[17] = {0}; be64(b, call_start + SZ_16K * 8); be64(b + 8, SZ_16K); setprop(OTHER, "reg", b, sizeof(b)); }
    check(false, "present competitor reg with trailing partial tuple");
    initialize(); memory_mode = MEMREMAP_WB; check(false, "WB classifier");
    initialize(); memory_mode = 0; check(false, "mixed or unknown classifier");
    initialize(); memory_mode = MEMREMAP_WB | MEMREMAP_WC; check(false, "invalid combined classifier");
    initialize(); mapped_pfn = 0; check(false, "first PFN still linearly mapped");
    initialize(); mapped_pfn = (int)(SZ_16K / PAGE_SIZE) - 1; check(false, "last PFN still linearly mapped");
    initialize(); call_start += 4096; check(false, "unaligned start");
    initialize(); call_size = 8192; check(false, "wrong size and alignment");
    initialize(); call_start = UINT64_MAX & ~(SZ_16K - 1); check(false, "extent arithmetic overflow");
    printf("{\"cases\":%u,\"page_shift\":%d,\"all_node_references_released\":true}\n", cases, PAGE_SHIFT);
    return 0;
}

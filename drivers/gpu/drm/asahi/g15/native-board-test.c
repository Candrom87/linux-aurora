// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Actual OF/Rust board preflight in the marked QEMU-only metadata fixture. */
#include <kunit/test.h>
#include <kunit/resource.h>
#include <linux/atomic.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include "native-power-fixture.h"

#define NATIVE_PROPERTY "asahi,g15c-native-board-data"
#define HEADER 16
#define CAPACITY 70
extern int asahi_g15_native_board_test(struct device *, u32, bool, u32 *);
extern const char *asahi_g15_native_board_test_prop(u32, u32 *);
extern int asahi_g15_native_bundle_images_test(struct device *, const void *, size_t, u32 *);
static atomic_t serial = ATOMIC_INIT(0);

struct fixture {
	struct kunit *test;
	struct of_changeset changes;
	struct device_node *node;
	bool applied;
};

static void cleanup_tree(void *data)
{
	struct fixture *f = data;

	if (f->applied)
		KUNIT_EXPECT_EQ(f->test, of_changeset_revert(&f->changes), 0);
	of_changeset_destroy(&f->changes);
	of_node_put(f->node);
}

static void cleanup_device(void *data) { platform_device_put(data); }
static void cleanup_node(void *data) { of_node_put(data); }

static int init(struct kunit *test)
{
	u32 marker;
	struct device_node *source;

	if (!of_machine_is_compatible("linux,dummy-virt") ||
	    of_property_read_u32(of_chosen, "qemu,gpu-linear-fixture", &marker) ||
	    marker != 0x45323433) {
		kunit_skip(test, "requires marked QEMU-only GPU metadata fixture");
		return 0;
	}
	source = of_find_node_by_path("/gpu");
	if (!source)
		return -ENOENT;
	test->priv = source;
	return kunit_add_action_or_reset(test, cleanup_node, source);
}

static size_t packet(u32 *cells, unsigned int count)
{
	static const u32 masks[] = {
		0x01234567, 0x89abcdef, 0x11112222, 0x33334444,
		0xfedcba98, 0x76543210, 0x9999aaaa, 0xbbbbcccc,
		0x80000000, 0x00000001, 0x55556666, 0x77778888,
	};
	unsigned int n;

	memset(cells, 0, CAPACITY * sizeof(*cells));
	cells[0] = 0x23c22001;
	cells[1] = 0x6034;
	cells[2] = 54321;
	cells[3] = count;
	memcpy(cells + 4, masks, sizeof(masks));
	for (n = 0; n < count; n++) {
		cells[HEADER + n] = min(n, 13U);
		cells[HEADER + count + n] = min(n, 13U);
		cells[HEADER + 2 * count + n] = 7 * n;
	}
	return 4 * (HEADER + 3 * count);
}

static void run(struct kunit *test, const u32 *cells, size_t bytes,
		u32 chip, bool full, int expected)
{
	struct fixture *f = kunit_kzalloc(test, sizeof(*f), GFP_KERNEL);
	struct platform_device *pdev;
	struct device_node *source = test->priv;
	u32 result[CAPACITY], value;
	char name[64];
	int ret, n;

	KUNIT_ASSERT_NOT_NULL(test, f);
	f->test = test;
	of_changeset_init(&f->changes);
	KUNIT_ASSERT_EQ(test, kunit_add_action_or_reset(test, cleanup_tree, f), 0);
	snprintf(name, sizeof(name), "/g15-native-board-kunit-%u",
		 (unsigned int)atomic_inc_return(&serial));
	f->node = of_changeset_create_node(&f->changes, of_root, name);
	KUNIT_ASSERT_NOT_NULL(test, f->node);
	KUNIT_ASSERT_EQ(test, of_changeset_add_prop_string(&f->changes,
		f->node, "status", "disabled"), 0);
	if (full) {
		static const char * const refs[] = { "apple,cs-opp", "apple,afr-opp" };

		/* Reuse the core tests' valid fictional controllers, including
		 * proper IEEE gains and consistent periods. No private calibration. */
		for (n = 0; n < 64; n++) {
			const char *key = asahi_g15_native_board_test_prop(n, &value);

			if (!key)
				break;
			KUNIT_ASSERT_EQ(test, of_changeset_add_prop_u32(&f->changes,
				f->node, key, value), 0);
		}
		KUNIT_ASSERT_EQ(test, n, 43);
		KUNIT_ASSERT_EQ(test, of_changeset_add_prop_u32(&f->changes,
			f->node, "apple,idleoff-standby-timer", 42), 0);
		/* Only auxiliary-table references come from the modeled handoff.
		 * No compatible, reg, binding or original GPU-node change. */
		for (n = 0; n < ARRAY_SIZE(refs); n++) {
			KUNIT_ASSERT_EQ(test, of_property_read_u32(source, refs[n], &value), 0);
			KUNIT_ASSERT_EQ(test, of_changeset_add_prop_u32(&f->changes,
				f->node, refs[n], value), 0);
		}
	}
	if (cells) {
		struct of_changeset_entry *entry;

		KUNIT_ASSERT_LE(test, bytes, CAPACITY * sizeof(*cells));
		KUNIT_ASSERT_EQ(test, of_changeset_add_prop_u32_array(&f->changes,
			f->node, NATIVE_PROPERTY, cells, DIV_ROUND_UP(bytes, 4)), 0);
		/* Keep the allocated copy bounded while exercising real malformed
		 * byte lengths through OF Property, before the Rust allocation. */
		entry = list_last_entry(&f->changes.entries,
			struct of_changeset_entry, node);
		entry->prop->length = bytes;
	}
	KUNIT_ASSERT_EQ(test, of_changeset_apply(&f->changes), 0);
	f->applied = true;
	pdev = platform_device_alloc("g15-native-board-kunit", PLATFORM_DEVID_NONE);
	KUNIT_ASSERT_NOT_NULL(test, pdev);
	pdev->dev.of_node = of_node_get(f->node);
	KUNIT_ASSERT_EQ(test, kunit_add_action_or_reset(test, cleanup_device, pdev), 0);
	KUNIT_ASSERT_EQ(test, dev_set_name(&pdev->dev, "g15-native-board-kunit-%u",
		(unsigned int)atomic_read(&serial)), 0);
	memset(result, 0xa5, sizeof(result));
	ret = asahi_g15_native_board_test(&pdev->dev, chip, full, result);
	KUNIT_EXPECT_EQ(test, ret, expected);
	if (ret > 0) {
		n = bytes / 4;
		KUNIT_EXPECT_MEMEQ(test, result, cells, bytes);
		if (full) {
			KUNIT_EXPECT_EQ(test, result[n], 0xb480U);
			KUNIT_EXPECT_EQ(test, result[n + 1], 15868U);
			KUNIT_EXPECT_EQ(test, result[n + 2], 2292U);
			KUNIT_EXPECT_EQ(test, result[n + 3], 0x6034U);
			KUNIT_EXPECT_EQ(test, result[n + 4], 6 * 0xd8000U);
			KUNIT_EXPECT_EQ(test, result[n + 5], 1300000U);
		}
		for (n = ret; n < CAPACITY; n++)
			KUNIT_EXPECT_EQ(test, result[n], 0xa5a5a5a5U);
	} else {
		for (n = 0; n < CAPACITY; n++)
			KUNIT_EXPECT_EQ(test, result[n], 0xa5a5a5a5U);
	}
	/* Device's OF reference must go before reverting/freeing the tree. */
	kunit_release_action(test, cleanup_device, pdev);
	kunit_release_action(test, cleanup_tree, f);
}

static void missing_property(struct kunit *test)
{
	run(test, NULL, 0, 0x6034, false, -ENOENT);
	run(test, NULL, 0, 0x6034, true, -ENOENT);
}

static void exact_roundtrip(struct kunit *test)
{
	u32 cells[CAPACITY];
	unsigned int count;

	for (count = 2; count <= 16; count++) {
		size_t bytes = packet(cells, count);

		run(test, cells, bytes, 0x6034, false, bytes / 4);
	}
}

static void malformed_lengths(struct kunit *test)
{
	static const size_t lengths[] = { 0, 1, 3, 4, 84, 87, 88, 89, 255, 257, 260 };
	u32 cells[CAPACITY];
	unsigned int n;

	packet(cells, 9);
	for (n = 0; n < ARRAY_SIZE(lengths); n++)
		run(test, cells, lengths[n], 0x6034, false, -EINVAL);
}

static void bad_contract_or_model(struct kunit *test)
{
	u32 cells[CAPACITY];
	size_t bytes = packet(cells, 9);

	cells[0] = 0;
	run(test, cells, bytes, 0x6034, false, -EINVAL);
	packet(cells, 9);
	cells[2] = 0;
	run(test, cells, bytes, 0x6034, false, -EINVAL);
}

static void bad_counts(struct kunit *test)
{
	static const u32 counts[] = { 0, 1, 17, U32_MAX };
	u32 cells[CAPACITY];
	unsigned int n;
	size_t bytes = packet(cells, 9);

	for (n = 0; n < ARRAY_SIZE(counts); n++) {
		cells[3] = counts[n];
		run(test, cells, bytes, 0x6034, false, -EINVAL);
	}
}

static void bad_maps_or_zero_state(struct kunit *test)
{
	static const unsigned int offsets[] = { 16, 17, 25, 26, 34 };
	u32 cells[CAPACITY];
	unsigned int n;

	for (n = 0; n < ARRAY_SIZE(offsets); n++) {
		size_t bytes = packet(cells, 9);

		cells[offsets[n]] = (offsets[n] == 17 || offsets[n] == 26) ? 14 : 1;
		run(test, cells, bytes, 0x6034, false, -EINVAL);
	}
}

static void packet_only_refuses_preflight(struct kunit *test)
{
	u32 cells[CAPACITY];
	size_t bytes = packet(cells, 14);

	run(test, cells, bytes, 0x6034, true, -EINVAL);
}

static void packet_only_changed_maps_refuse(struct kunit *test)
{
	u32 cells[CAPACITY];
	size_t bytes = packet(cells, 14);

	cells[16 + 5] = 4;
	run(test, cells, bytes, 0x6034, true, -EINVAL);
	packet(cells, 14);
	cells[16 + 14 + 5] = 4;
	run(test, cells, bytes, 0x6034, true, -EINVAL);
}

static void chip_scope(struct kunit *test)
{
	u32 cells[CAPACITY];
	size_t bytes = packet(cells, 9);

	run(test, cells, bytes, 0x6031, false, -EINVAL);
	run(test, cells, bytes, 0, false, -EINVAL);
	cells[1] = 0x6031;
	run(test, cells, bytes, 0x6034, true, -EINVAL);
}

static void native_bundle_images(struct kunit *test)
{
	struct fixture *f = kunit_kzalloc(test, sizeof(*f), GFP_KERNEL);
	struct g15c_power_fixture *input = g15c_power_fixture_new(test);
	struct g15c_board_bundle *bundle = kunit_kmalloc(test, sizeof(*bundle), GFP_KERNEL);
	struct platform_device *pdev;
	u32 output[6], value;
	char name[64];
	int n;

	KUNIT_ASSERT_NOT_NULL(test, f);
	KUNIT_ASSERT_NOT_NULL(test, input);
	KUNIT_ASSERT_NOT_NULL(test, bundle);
	/* Keep the separately qualified base-state invariant while retaining a
	 * repeated/nonmonotonic primary map and independent secondary stream. */
	input->input.map_banks[0][0] &= ~(15U << 16);
	input->input.map_banks[0][0] |= 1U << 16;
	KUNIT_ASSERT_EQ(test, asahi_g15c_source_bundle(&input->input, 0x6034, bundle), G15C_OK);
	f->test = test;
	of_changeset_init(&f->changes);
	KUNIT_ASSERT_EQ(test, kunit_add_action_or_reset(test, cleanup_tree, f), 0);
	snprintf(name, sizeof(name), "/g15-native-images-kunit-%u",
		 (unsigned int)atomic_inc_return(&serial));
	f->node = of_changeset_create_node(&f->changes, of_root, name);
	KUNIT_ASSERT_NOT_NULL(test, f->node);
	KUNIT_ASSERT_EQ(test, of_changeset_add_prop_string(&f->changes, f->node, "status", "disabled"), 0);
	for (n = 0; n < 64; n++) {
		const char *key = asahi_g15_native_board_test_prop(n, &value);

		if (!key)
			break;
		if (!strcmp(key, "apple,min-sram-microvolt") ||
		    !strcmp(key, "apple,csafr-min-sram-microvolt"))
			value = 1400000;
		KUNIT_ASSERT_EQ(test, of_changeset_add_prop_u32(&f->changes, f->node, key, value), 0);
	}
	KUNIT_ASSERT_EQ(test, n, 43);
	KUNIT_ASSERT_EQ(test, of_changeset_add_prop_u32(&f->changes,
		f->node, "apple,idleoff-standby-timer", 42), 0);
	/* No OPP phandles or short native packet: the owned bundle supplies all
	 * performance data. The device stays unregistered and disabled. */
	KUNIT_ASSERT_EQ(test, of_changeset_apply(&f->changes), 0);
	f->applied = true;
	pdev = platform_device_alloc("g15-native-images-kunit", PLATFORM_DEVID_NONE);
	KUNIT_ASSERT_NOT_NULL(test, pdev);
	pdev->dev.of_node = of_node_get(f->node);
	KUNIT_ASSERT_EQ(test, kunit_add_action_or_reset(test, cleanup_device, pdev), 0);
	KUNIT_ASSERT_EQ(test, dev_set_name(&pdev->dev, "g15-native-images-kunit-%u",
		(unsigned int)atomic_read(&serial)), 0);
	KUNIT_ASSERT_EQ(test, asahi_g15_native_bundle_images_test(&pdev->dev,
		bundle, sizeof(*bundle), output), 6);
	KUNIT_EXPECT_EQ(test, output[0], 0xb480U);
	KUNIT_EXPECT_EQ(test, output[1], 15868U);
	KUNIT_EXPECT_EQ(test, output[2], 2292U);
	KUNIT_EXPECT_EQ(test, output[3], 0x6034U);
	KUNIT_EXPECT_EQ(test, output[4], 6U * 0xd8000);
	KUNIT_EXPECT_EQ(test, output[5], 713000U);
	/* The bound packet and table geometry must agree before image writes. */
	bundle->tables.caps[0] = SELECTED;
	memset(output, 0xa5, sizeof(output));
	KUNIT_EXPECT_EQ(test, asahi_g15_native_bundle_images_test(&pdev->dev,
		bundle, sizeof(*bundle), output), -EINVAL);
	KUNIT_EXPECT_PTR_EQ(test, memchr_inv(output, 0xa5, sizeof(output)), NULL);
}

static struct kunit_case cases[] = {
	KUNIT_CASE(missing_property),
	KUNIT_CASE(exact_roundtrip),
	KUNIT_CASE(malformed_lengths),
	KUNIT_CASE(bad_contract_or_model),
	KUNIT_CASE(bad_counts),
	KUNIT_CASE(bad_maps_or_zero_state),
	KUNIT_CASE(packet_only_refuses_preflight),
	KUNIT_CASE(packet_only_changed_maps_refuse),
	KUNIT_CASE(chip_scope),
	KUNIT_CASE(native_bundle_images),
	{}
};

static struct kunit_suite suite = {
	.name = "asahi-g15-native-board",
	.init = init,
	.test_cases = cases,
};
kunit_test_suite(suite);
MODULE_LICENSE("Dual MIT/GPL");

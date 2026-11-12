// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Explicit-input CPU arithmetic with real kernel FPU ownership, QEMU only. */
#include <kunit/test.h>
#include <linux/fpu.h>
#include <linux/of.h>
#include <linux/preempt.h>
#include <linux/string.h>
#include <linux/unaligned.h>

#include "power/board_source.h"

#define RAW 14
#define SELECTED 9
#define CS_COUNT 7
#define AFR_COUNT 6

struct fixture {
	struct g15c_board_source_input input;
	u8 main[4 * RAW * 8], sram[4 * RAW * 8];
	u8 cs[16 + CS_COUNT * 16 + 8], afr[16 + AFR_COUNT * 16 + 8];
};

static int init(struct kunit *test)
{
	u32 marker;

	if (!of_machine_is_compatible("linux,dummy-virt") ||
	    of_property_read_u32(of_chosen, "qemu,gpu-linear-fixture", &marker) ||
	    marker != 0x45323433) {
		kunit_skip(test, "requires marked QEMU-only metadata fixture");
		return 0;
	}
	return 0;
}

static void aux(u8 *data, u32 count, u32 offset)
{
	u32 row;

	put_unaligned_le64(1, data);
	put_unaligned_le64(count, data + 8);
	for (row = 0; row < count; row++) {
		put_unaligned_le64((600 + row * 20) * 1000 + 999, data + 16 + row * 16);
		put_unaligned_le64(24000000 + row * 31000000 + offset,
				   data + 24 + row * 16);
	}
	put_unaligned_le64(789999, data + 16 + count * 16);
}

static struct fixture *fixture(struct kunit *test)
{
	static const u32 primary[SELECTED] = { 0, 2, 4, 6, 8, 10, 12, 13, 12 };
	static const u32 secondary[SELECTED] = { 0, 1, 3, 5, 7, 9, 11, 12, 13 };
	struct fixture *f = kunit_kzalloc(test, sizeof(*f), GFP_KERNEL);
	u32 lane, row, bank;

	if (!f)
		return NULL;
	f->input.contract = G15C_23J220_CONTRACT;
	f->input.chip_id = 0x6034;
	f->input.gpu_num_perf_states = RAW - 1;
	f->input.table_count = 4;
	f->input.identity[0] = 0x07040000;
	f->input.identity[1] = 0x0001040a;
	f->input.identity[3] = 0x12345678;
	f->input.identity[4] = 0x56;
	f->input.coefficients = (struct g15c_packed_coefficients) {
		.w124 = 0x12345678, .w128 = 0x23456789,
		.w12c = 0x3456789a, .w134 = 0x456789ab, .present = 15,
	};
	f->input.cs_activity_mask = 0x12345;
	f->input.masks_present = 7;
	for (bank = 0; bank < 3; bank++)
		for (lane = 0; lane < 4; lane++)
			f->input.masks_msb[bank][lane] = 0x80000001 + bank * 32 + lane;
	for (row = 1; row < SELECTED; row++) {
		f->input.map_banks[0][row / 2] |= primary[row] << (16 * (row % 2));
		f->input.map_banks[1][row / 2] |= secondary[row] << (16 * (row % 2));
	}
	for (lane = 0; lane < 4; lane++) {
		for (row = 0; row < RAW; row++) {
			u32 at = (lane * RAW + row) * 8;

			put_unaligned_le32(24000000 + row * 53000000 + lane * 17,
					   f->main + at);
			put_unaligned_le32(500 + row * 37 + lane * 3, f->main + at + 4);
			put_unaligned_le32(24000000 + row * 53000000 + lane * 19 + 1001,
					   f->sram + at);
			put_unaligned_le32(900 + row + lane * 7, f->sram + at + 4);
		}
	}
	aux(f->cs, CS_COUNT, 0);
	aux(f->afr, AFR_COUNT, 123456);
	f->input.main = (struct g15c_property) { f->main, sizeof(f->main) };
	f->input.sram = (struct g15c_property) { f->sram, sizeof(f->sram) };
	f->input.cs = (struct g15c_property) { f->cs, sizeof(f->cs) };
	f->input.afr = (struct g15c_property) { f->afr, sizeof(f->afr) };
	return f;
}

static struct g15c_board_bundle *output(struct kunit *test)
{
	struct g15c_board_bundle *out = kunit_kmalloc(test, sizeof(*out), GFP_KERNEL);

	if (out)
		memset(out, 0xa5, sizeof(*out));
	return out;
}

static void unchanged(struct kunit *test, const struct g15c_board_bundle *out)
{
	KUNIT_EXPECT_PTR_EQ(test, memchr_inv(out, 0xa5, sizeof(*out)), NULL);
}

static void decode(struct kunit *test)
{
	struct g15c_packed_coefficients in = { .present = 15 };
	struct g15c_coefficients out;
	u32 lane;

	KUNIT_ASSERT_EQ(test, asahi_g15c_decode(&in, &out), G15C_OK);
	for (lane = 0; lane < 4; lane++)
		KUNIT_EXPECT_EQ(test, out.main_bits[lane], 0x40000000U);
	KUNIT_EXPECT_EQ(test, out.cs_bits[0], 0x3f800000U);
	KUNIT_EXPECT_EQ(test, out.cs_bits[1], 0U);
	KUNIT_EXPECT_EQ(test, out.main_present, 15U);
	KUNIT_EXPECT_EQ(test, out.cs_present, 1U);
	in.present = 7;
	memset(&out, 0xa5, sizeof(out));
	KUNIT_EXPECT_EQ(test, asahi_g15c_decode(&in, &out), G15C_MISSING);
	KUNIT_EXPECT_PTR_EQ(test, memchr_inv(&out, 0xa5, sizeof(out)), NULL);
}

static void bundle(struct kunit *test)
{
	static const u32 primary[SELECTED] = { 0, 2, 4, 6, 8, 10, 12, 13, 12 };
	static const u32 secondary[SELECTED] = { 0, 1, 3, 5, 7, 9, 11, 12, 13 };
	struct fixture *f = fixture(test);
	struct g15c_board_bundle *out = output(test);
	u32 row, lane, mask;
	const u8 *bytes;

	KUNIT_ASSERT_NOT_NULL(test, f);
	KUNIT_ASSERT_NOT_NULL(test, out);
	KUNIT_ASSERT_EQ(test, asahi_g15c_source_bundle(&f->input, 0x6034, out), G15C_OK);
	bytes = out->packet.bytes;
	KUNIT_ASSERT_EQ(test, out->packet.length, 4U * (16 + 3 * SELECTED));
	KUNIT_EXPECT_EQ(test, get_unaligned_be32(bytes), G15C_23J220_CONTRACT);
	KUNIT_EXPECT_EQ(test, get_unaligned_be32(bytes + 4), 0x6034U);
	KUNIT_EXPECT_GT(test, get_unaligned_be32(bytes + 8), 0U);
	KUNIT_EXPECT_EQ(test, get_unaligned_be32(bytes + 12), (u32)SELECTED);
	for (mask = 0; mask < 3; mask++)
		for (lane = 0; lane < 4; lane++)
			KUNIT_EXPECT_EQ(test, get_unaligned_be32(bytes + 16 + (mask * 4 + lane) * 4),
					f->input.masks_msb[mask][lane]);
	for (row = 0; row < SELECTED; row++) {
		KUNIT_EXPECT_EQ(test, get_unaligned_be32(bytes + 64 + row * 4), primary[row]);
		KUNIT_EXPECT_EQ(test, get_unaligned_be32(bytes + 64 + (SELECTED + row) * 4), secondary[row]);
	}
	KUNIT_EXPECT_EQ(test, get_unaligned_be32(bytes + 64 + 2 * SELECTED * 4), 0U);
	KUNIT_EXPECT_EQ(test, get_unaligned_be32(bytes + out->packet.length - 4), 100U);
	KUNIT_EXPECT_PTR_EQ(test, memchr_inv(bytes + out->packet.length, 0,
					   sizeof(out->packet.bytes) - out->packet.length), NULL);
	KUNIT_EXPECT_EQ(test, out->tables.main.count, (u32)RAW);
	KUNIT_EXPECT_EQ(test, out->tables.main.tables, 4U);
	for (row = 0; row < RAW; row++) {
		KUNIT_EXPECT_EQ(test, out->tables.main.hz[row], 24000000U + row * 53000000 + 3 * 19 + 1001);
		for (lane = 0; lane < 8; lane++) {
			KUNIT_EXPECT_EQ(test, out->tables.main.mv[row][lane],
					500U + row * 37 + (lane < 4 ? lane * 3 : 0));
			KUNIT_EXPECT_EQ(test, out->tables.main.sram_mv[row][lane],
					900U + row + (lane < 4 ? lane * 7 : 0));
		}
	}
	KUNIT_EXPECT_EQ(test, out->tables.cs.count, (u32)CS_COUNT);
	KUNIT_EXPECT_EQ(test, out->tables.afr.count, (u32)AFR_COUNT);
	for (row = 0; row < CS_COUNT; row++) {
		KUNIT_EXPECT_EQ(test, out->tables.cs.mv[row][0], 600U + row * 20);
		KUNIT_EXPECT_EQ(test, out->tables.cs.sram_mv[row][0], 789U);
	}
	KUNIT_EXPECT_EQ(test, out->tables.caps[0], 7U);
	KUNIT_EXPECT_EQ(test, out->tables.caps[1], 5U);
	KUNIT_EXPECT_EQ(test, out->tables.caps[2], 5U);
}

static void revision_caps(struct kunit *test)
{
	struct fixture *f = fixture(test);
	struct g15c_board_bundle *out = output(test);
	static const u32 versions[] = { 0x07041000, 0x07041100, 0x07042000, 0x0704f000 };
	u32 i;

	KUNIT_ASSERT_NOT_NULL(test, f);
	KUNIT_ASSERT_NOT_NULL(test, out);
	for (i = 0; i < ARRAY_SIZE(versions); i++) {
		f->input.identity[0] = versions[i];
		KUNIT_ASSERT_EQ(test, asahi_g15c_source_bundle(&f->input, 0x6034, out), G15C_OK);
		KUNIT_EXPECT_EQ(test, out->tables.caps[0], 8U);
		KUNIT_EXPECT_EQ(test, out->tables.caps[1], 6U);
		KUNIT_EXPECT_EQ(test, out->tables.caps[2], 5U);
	}
	/* Revision-one caps must fit their independently imported tables. This
	 * refusal follows successful numerical work, before caller publication. */
	f->input.identity[0] = 0x07040000;
	aux(f->afr, 5, 123456);
	memset(out, 0xa5, sizeof(*out));
	KUNIT_EXPECT_EQ(test, asahi_g15c_source_bundle(&f->input, 0x6034, out), G15C_INVALID);
	unchanged(test, out);
}

static void malformed_import(struct kunit *test)
{
	struct fixture *f = fixture(test);
	struct g15c_board_bundle *out = output(test);
	struct g15c_property *props;
	u32 i;

	KUNIT_ASSERT_NOT_NULL(test, f);
	KUNIT_ASSERT_NOT_NULL(test, out);
	/* Four individually named members, accessed explicitly rather than
	 * assuming a C array spans distinct struct members. */
	for (i = 0; i < 4; i++) {
		size_t saved;

		props = i == 0 ? &f->input.main : i == 1 ? &f->input.sram :
			i == 2 ? &f->input.cs : &f->input.afr;
		saved = props->length;
		props->length = 0;
		KUNIT_EXPECT_EQ(test, asahi_g15c_source_bundle(&f->input, 0x6034, out), G15C_INVALID);
		unchanged(test, out);
		props->length = saved;
	}
	f->input.sram.bytes = NULL;
	KUNIT_EXPECT_EQ(test, asahi_g15c_source_bundle(&f->input, 0x6034, out), G15C_INVALID);
	unchanged(test, out);
}

static void shared_rail(struct kunit *test)
{
	struct fixture *f = fixture(test);
	struct g15c_board_bundle *out = output(test);

	KUNIT_ASSERT_NOT_NULL(test, f);
	KUNIT_ASSERT_NOT_NULL(test, out);
	put_unaligned_le64(799999, f->afr + 16 + AFR_COUNT * 16);
	KUNIT_EXPECT_EQ(test, asahi_g15c_source_bundle(&f->input, 0x6034, out), G15C_INVALID);
	unchanged(test, out);
	aux(f->afr, AFR_COUNT, 123456);
	put_unaligned_le64(601999, f->afr + 16);
	KUNIT_EXPECT_EQ(test, asahi_g15c_source_bundle(&f->input, 0x6034, out), G15C_INVALID);
	unchanged(test, out);
}

static void refused_context(struct kunit *test)
{
	struct fixture *f = fixture(test);
	struct g15c_board_bundle *out = output(test);
	unsigned long flags;
	int ret;

	KUNIT_ASSERT_NOT_NULL(test, f);
	KUNIT_ASSERT_NOT_NULL(test, out);
	preempt_disable();
	ret = asahi_g15c_source_bundle(&f->input, 0x6034, out);
	preempt_enable();
	KUNIT_EXPECT_EQ(test, ret, -EWOULDBLOCK);
	unchanged(test, out);
	KUNIT_ASSERT_TRUE(test, kernel_fpu_available());
	kernel_fpu_begin();
	ret = asahi_g15c_source_bundle(&f->input, 0x6034, out);
	kernel_fpu_end();
	KUNIT_EXPECT_EQ(test, ret, -EWOULDBLOCK);
	unchanged(test, out);
	local_irq_save(flags);
	ret = asahi_g15c_source_bundle(&f->input, 0x6034, out);
	local_irq_restore(flags);
	KUNIT_EXPECT_EQ(test, ret, -EWOULDBLOCK);
	unchanged(test, out);
}

static void late_failure(struct kunit *test)
{
	struct fixture *f = fixture(test);
	struct g15c_board_bundle *out = output(test);

	KUNIT_ASSERT_NOT_NULL(test, f);
	KUNIT_ASSERT_NOT_NULL(test, out);
	put_unaligned_le32(1501, f->main + (2 * RAW + 12) * 8 + 4);
	KUNIT_EXPECT_EQ(test, asahi_g15c_source_bundle(&f->input, 0x6034, out), G15C_INVALID);
	unchanged(test, out);
}

static void repeat_owned_input(struct kunit *test)
{
	struct fixture *f = fixture(test), *before;
	struct g15c_board_bundle *first = output(test), *second = output(test);

	KUNIT_ASSERT_NOT_NULL(test, f);
	KUNIT_ASSERT_NOT_NULL(test, first);
	KUNIT_ASSERT_NOT_NULL(test, second);
	before = kunit_kmalloc(test, sizeof(*f), GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, before);
	memcpy(before, f, sizeof(*f));
	KUNIT_ASSERT_EQ(test, asahi_g15c_source_bundle(&f->input, 0x6034, first), G15C_OK);
	KUNIT_ASSERT_EQ(test, asahi_g15c_source_bundle(&f->input, 0x6034, second), G15C_OK);
	KUNIT_EXPECT_MEMEQ(test, first, second, sizeof(*first));
	KUNIT_EXPECT_MEMEQ(test, f, before, sizeof(*f));
}

static struct kunit_case cases[] = {
	KUNIT_CASE(decode), KUNIT_CASE(bundle), KUNIT_CASE(revision_caps),
	KUNIT_CASE(malformed_import), KUNIT_CASE(shared_rail),
	KUNIT_CASE(refused_context), KUNIT_CASE(late_failure),
	KUNIT_CASE(repeat_owned_input), {}
};

static struct kunit_suite suite = {
	.name = "asahi-g15-native-power", .init = init, .test_cases = cases,
};
kunit_test_suite(suite);

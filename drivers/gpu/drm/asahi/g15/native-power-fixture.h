// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Invented CPU-only source inputs shared by the arithmetic and image suites. */
#ifndef ASAHI_G15_NATIVE_POWER_FIXTURE_H
#define ASAHI_G15_NATIVE_POWER_FIXTURE_H
#include <kunit/test.h>
#include <linux/unaligned.h>
#include "power/board_source.h"

#define RAW 14
#define SELECTED 9
#define CS_COUNT 7
#define AFR_COUNT 6

struct g15c_power_fixture {
	struct g15c_board_source_input input;
	u8 main[4 * RAW * 8], sram[4 * RAW * 8];
	u8 cs[16 + CS_COUNT * 16 + 8], afr[16 + AFR_COUNT * 16 + 8];
};

static void g15c_power_aux(u8 *data, u32 count, u32 offset)
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

static struct g15c_power_fixture *g15c_power_fixture_new(struct kunit *test)
{
	static const u32 primary[SELECTED] = { 0, 2, 4, 6, 8, 10, 12, 13, 12 };
	static const u32 secondary[SELECTED] = { 0, 1, 3, 5, 7, 9, 11, 12, 13 };
	struct g15c_power_fixture *f = kunit_kzalloc(test, sizeof(*f), GFP_KERNEL);
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
	g15c_power_aux(f->cs, CS_COUNT, 0);
	g15c_power_aux(f->afr, AFR_COUNT, 123456);
	f->input.main = (struct g15c_property) { f->main, sizeof(f->main) };
	f->input.sram = (struct g15c_property) { f->sram, sizeof(f->sram) };
	f->input.cs = (struct g15c_property) { f->cs, sizeof(f->cs) };
	f->input.afr = (struct g15c_property) { f->afr, sizeof(f->afr) };
	return f;
}

#endif

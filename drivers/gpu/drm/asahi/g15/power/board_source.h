/* SPDX-License-Identifier: MIT */
/* Integer source adapter from m1n1 E106; no FDT writer is part of this port. */
#ifndef ASAHI_G15C_BOARD_SOURCE_H
#define ASAHI_G15C_BOARD_SOURCE_H
#include "native.h"
#include "native_import.h"
/* All spans and words have already been acquired by a future qualified caller.
 * This type authenticates neither their provenance nor a same-boot snapshot.
 * The source-scoped branch is 23J220, one CS channel, four main lanes, raw masks.
 */
struct g15c_board_source_input {
	uint32_t contract, chip_id, gpu_num_perf_states, table_count;
	struct g15c_property main, sram, cs, afr;
	uint32_t identity[6];
	struct g15c_packed_coefficients coefficients;
	uint32_t map_banks[2][8], cs_activity_mask;
	uint32_t masks_msb[3][4], masks_present;
};
int g15c_23j220_board_source(const struct g15c_board_source_input *,
			    struct g15c_board_packet_input *);
#endif

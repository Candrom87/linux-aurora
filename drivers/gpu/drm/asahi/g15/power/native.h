/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
#ifndef ASAHI_G15C_NATIVE_POWER_H
#define ASAHI_G15C_NATIVE_POWER_H
#include "g15c_23j220_power.h"
#define G15C_BOARD_RAW_STATES 14
#define G15C_BOARD_PACKET_BYTES 256
struct g15c_board_packet_input {
	struct g15c_power_input source;
	uint32_t map_banks[2][8];
	uint32_t chip_id;
	uint32_t masks_msb[3][4];
	uint32_t masks_present;
};
struct g15c_board_packet {
	uint32_t length;
	uint8_t bytes[G15C_BOARD_PACKET_BYTES];
};
/* Sleepable process context only. Integer ABI; no source acquisition or board
 * admission. Caller owns immutable input and disjoint writable output. Errors
 * preserve every output byte. Return G15C_* for model refusals, Linux errno for
 * context/allocation failures. No GPU, firmware, MMIO, OF or device interaction.
 */
int asahi_g15c_decode(const struct g15c_packed_coefficients *,
		     struct g15c_coefficients *);
int asahi_g15c_board_packet(const struct g15c_board_packet_input *, uint32_t,
			   struct g15c_board_packet *);
struct g15c_board_source_input;
int asahi_g15c_source_packet(const struct g15c_board_source_input *, uint32_t,
			    struct g15c_board_packet *);
#endif

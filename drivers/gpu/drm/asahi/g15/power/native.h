/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
#ifndef ASAHI_G15C_NATIVE_POWER_H
#define ASAHI_G15C_NATIVE_POWER_H
#include "g15c_23j220_power.h"
#include "native_import.h"
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
struct g15c_native_tables {
	struct g15c_main_import main;
	struct g15c_aux_import cs, afr;
	uint32_t caps[3]; /* main, CS, AFR; separate from imported table counts */
};
struct g15c_board_bundle {
	struct g15c_board_packet packet;
	struct g15c_native_tables tables;
};
struct g15c_cap_context {
	uint32_t selector, revision;
};
/* Source-scoped zero-initialized probe -> readChipInfo -> first-word copy.
 * Derives the old CoreConfig fields from supplied identity words, not a new
 * read or source attestation. Unknown revision encodings retain source zero.
 */
int g15c_native_context(const uint32_t identity[6], struct g15c_cap_context *);
/* Explicit old-host CoreConfig selector/revision, not an asserted GPU ID word.
 * On selector0x18/revision1, the pinned source uses caps7/5/5. Other revisions
 * use each imported count minus one. Refuse caps outside supplied tables.
 * This does not acquire or authenticate that CoreConfig context.
 */
int g15c_native_caps(uint32_t selector, uint32_t revision,
		     uint32_t main, uint32_t cs, uint32_t afr, uint32_t caps[3]);
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
int asahi_g15c_source_bundle(const struct g15c_board_source_input *, uint32_t,
			    struct g15c_board_bundle *);
#endif

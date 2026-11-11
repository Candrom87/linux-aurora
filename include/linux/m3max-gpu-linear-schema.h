/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
#ifndef M3MAX_GPU_LINEAR_SCHEMA_H
#define M3MAX_GPU_LINEAR_SCHEMA_H
#define M3G_PROPERTY "asahi,t6034-efi-gpu-memory"
#define M3G_VERSION 1
#define M3G_UAT_COUNT 4
#define M3G_FW_COUNT 2
#define M3G_REGION_COUNT 6
#define M3G_FW_UPPER 0xfffffc0000000000ULL
/* u64 big-endian cells. UAT order is the kernel's named-region order:
 * TTBs, pagetables, handoff, pagetables-l2. Firmware order is TEXT, DATA.
 * Describes a post-EFI producer result, not firmware attestation.
 */
enum m3g_cell {
	M3G_SCHEMA, M3G_COUNT, M3G_MAP_BASE, M3G_MAP_BYTES,
	M3G_MAP_STRIDE, M3G_MAP_VERSION, M3G_DRAM_BASE, M3G_DRAM_BYTES,
	M3G_RANGES, M3G_CELLS = M3G_RANGES + 2 * M3G_REGION_COUNT,
};
#endif

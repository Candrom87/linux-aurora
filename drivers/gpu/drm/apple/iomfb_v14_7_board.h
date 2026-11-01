/* SPDX-License-Identifier: GPL-2.0-only OR MIT */

#ifndef __APPLE_IOMFB_V14_7_BOARD_H__
#define __APPLE_IOMFB_V14_7_BOARD_H__

#include <linux/types.h>
#include <linux/overflow.h>

/*
 * What differs between the internal panels the 14.x IOMFB drives. The T6030
 * record holds the values the IOMFB code was written and tested with (J514S,
 * J516S); another board's record enables only what was tested on it, and
 * anything else fails closed. External processors have no board record.
 */
struct dcp_v14_board {
	/* In log messages. */
	const char *name;
	/* The debugfs status file. */
	const char *debugfs;
	const char *dcp_compatible;
	/* The machine compatible; NULL for any machine with this DCP. */
	const char *machine;
	/* The firmware image whose layouts were tested on this board. */
	const char *firmware_uuid;
	/* Set to <1> by the boot loader on the DCP, display and PIODMA nodes. */
	const char *handoff;
	/* Native panel size, notch rows included; 0: any, from the boot framebuffer. */
	u32 panel_width, panel_height;
	/* The panel may have a 120 Hz timing besides 60 Hz. */
	bool promotion;
	/* Until this board's V14 adaptive-sync path is implemented and tested. */
	bool vrr_unqualified;
	/* The internal firmware's boot timing has not been measured on this board. */
	bool boot_timing_unqualified;
	/*
	 * Log what the first boots of a new board are judged by, not only with
	 * debugging on: every panel timing the firmware offers, and the display
	 * clock it is told.
	 */
	bool log_bringup;
	/*
	 * The colour matrix setter and getter of this board's firmware image;
	 * 0: the matrix is not sent. Method numbers differ between firmware
	 * releases, so they are only set where tested.
	 */
	u32 ctm_set, ctm_get;
	/*
	 * The panel gets a base EDID (make, model and size) built from its
	 * device-tree node, as the firmware gives none.
	 */
	bool base_edid;
	/*
	 * The panel's swaps carry the constant timestamp
	 * DCP_V14_SWAP_TIMESTAMP_VALUE in slots 0, 1 and 6. With zero
	 * timestamps this board's firmware completes at most one swap per
	 * 16.7 ms, so a 120 Hz panel presents at 60 fps.
	 */
	bool swap_timestamps;
	/* Unqualified timing experiment, requiring an explicit test switch. */
	bool experimental_timed_swaps;
	/* An unqualified internal timing RPC can be requested explicitly. */
	bool experimental_timing_request;
	/* This board has a qualified panel level block; machine admission is separate. */
	bool panel_nits;
};

/* Whether the DCP node or the machine (as @ctx gives them) has @compat. */
typedef bool (*dcp_v14_compat_fn)(const void *ctx, const char *compat);

/*
 * The board record of a DCP. NULL if there is none: *@dcp_known then says
 * whether a record names the DCP's compatible (but no record is for this
 * machine) or none does (not an internal 14.x DCP).
 */
const struct dcp_v14_board *dcp_v14_board_select(dcp_v14_compat_fn dcp_is,
						 dcp_v14_compat_fn machine_is,
						 const void *ctx, bool *dcp_known);

/*
 * Whether @board runs a DCP with the firmware image @uuid (NULL: none given):
 * only the image whose IOMFB layouts were validated for it.
 */
bool dcp_v14_board_admits(const struct dcp_v14_board *board, const char *uuid);

/*
 * Whether a swap carries timestamps: only the internal panel of a board whose
 * record asks for them. External processors (@external, no record) and every
 * other board send zero timestamps.
 */
bool dcp_v14_board_stamps_swaps(const struct dcp_v14_board *board, bool external,
				bool experiment, unsigned int advertised_hz);

/* Reconstruct the full height from a cropped or full-height boot framebuffer. */
static inline bool dcp_v14_panel_height(const struct dcp_v14_board *board,
					u32 fb_height, u32 notch, u32 *height)
{
	if (board->panel_height && notch && fb_height == board->panel_height) {
		*height = fb_height;
		return true;
	}
	return !check_add_overflow(fb_height, notch, height);
}

static inline bool dcp_v14_board_allows_vrr(const struct dcp_v14_board *board,
					    bool requested)
{
	return !requested || !board || !board->vrr_unqualified;
}

static inline bool dcp_v14_board_requests_120(const struct dcp_v14_board *board,
					      bool external, bool experiment, unsigned int hz)
{
	return board && board->experimental_timing_request && !external && experiment && hz == 120;
}

/* For the experiment, prefer an advertised 120 Hz mode over a preferred
 * 60 Hz mode. Otherwise preserve the existing preferred-mode ordering. */
static inline bool dcp_v14_choose_timing(const struct dcp_v14_board *board, bool experiment,
					 unsigned int hz, bool preferred,
					 bool have_best, unsigned int best_hz)
{
	bool candidate_120 = dcp_v14_board_requests_120(board, false, experiment, hz);
	bool best_120 = dcp_v14_board_requests_120(board, false, experiment, best_hz);

	if (!have_best)
		return true;
	if (candidate_120 != best_120)
		return candidate_120;
	return preferred;
}

#endif /* __APPLE_IOMFB_V14_7_BOARD_H__ */

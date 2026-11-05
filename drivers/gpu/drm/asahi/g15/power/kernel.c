// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Integer-only ownership boundary. No platform caller is admitted by this API. */
#include <linux/errno.h>
#include <linux/fpu.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <asm/simd.h>
#include <asm/sysreg.h>
#include "native.h"
#include "native_select.h"
#include "port.h"
#include "board_source.h"

struct packet_work {
	struct g15c_power_input selected;
	struct g15c_power_output power;
	struct g15c_native_selection selection;
	struct g15c_board_packet packet;
};

/* Allocating entry points require sleepable process context. In particular,
 * refuse nested task FPSIMD use before the generic API's BUG_ON path.
 */
static int context_allowed(void)
{
	if (!in_task() || irqs_disabled() || in_atomic() ||
	    test_thread_flag(TIF_KERNEL_FPSTATE))
		return -EWOULDBLOCK;
	if (!system_capabilities_finalized() || !kernel_fpu_available())
		return -EOPNOTSUPP;
	return 0;
}

/* Pin before the final admission check. kernel_fpu_begin/end add/remove their
 * own preemption level; our outer level keeps the check and ownership atomic
 * with respect to task migration. Softirq FPSIMD nesting is handled by arm64.
 */
static int owned_model(const void *input, void *output, bool decode)
{
	u64 fpcr, fpsr;
	int ret;

	preempt_disable();
	if (!may_use_simd() || test_thread_flag(TIF_KERNEL_FPSTATE)) {
		preempt_enable();
		return -EWOULDBLOCK;
	}
	kernel_fpu_begin();
	fpcr = read_sysreg_s(SYS_FPCR);
	fpsr = read_sysreg_s(SYS_FPSR);
	write_sysreg_s(0, SYS_FPCR);
	write_sysreg_s(0, SYS_FPSR);
	/* As in fpsimd_load_common(), these architectural FP controls take effect
	 * in instruction order. No vendor FP control is accessed or modified.
	 */
	if (!g15c_port_ieee_environment())
		ret = G15C_NUMERIC;
	else if (decode)
		ret = g15c_core_decode(input, output);
	else
		ret = g15c_core_produce(input, output);
	write_sysreg_s(fpsr, SYS_FPSR);
	write_sysreg_s(fpcr, SYS_FPCR);
	kernel_fpu_end();
	preempt_enable();
	return ret;
}

int asahi_g15c_source_packet(const struct g15c_board_source_input *in,
			    u32 expected_chip, struct g15c_board_packet *out)
{
	struct g15c_board_packet_input *prepared;
	int ret;

	if (!in || !out || !expected_chip || in->chip_id != expected_chip)
		return G15C_INVALID;
	ret = context_allowed();
	if (ret)
		return ret;
	prepared = kzalloc(sizeof(*prepared), GFP_KERNEL);
	if (!prepared)
		return -ENOMEM;
	/* Import/rail checks and coefficient decode complete before packet compute.
	 * Only decode and model arithmetic enter their own owned FPU sections.
	 */
	ret = g15c_23j220_board_source(in, prepared);
	if (!ret)
		ret = asahi_g15c_board_packet(prepared, expected_chip, out);
	kfree_sensitive(prepared);
	return ret;
}

int asahi_g15c_source_bundle(const struct g15c_board_source_input *in,
			    u32 expected_chip, u32 selector, u32 revision,
			    struct g15c_board_bundle *out)
{
	struct g15c_board_bundle *result;
	int ret;

	if (!in || !out || !expected_chip || in->chip_id != expected_chip)
		return G15C_INVALID;
	ret = context_allowed();
	if (ret)
		return ret;
	result = kzalloc(sizeof(*result), GFP_KERNEL);
	if (!result)
		return -ENOMEM;
	/* The caller's immutable spans are one source snapshot. Preserve the raw
	 * native tables as well as the calculated packet; never reconstruct SRAM
	 * from an OPP voltage/floor. Reimporting these bounded immutable buffers
	 * is not another acquisition and stays entirely outside owned FP sections.
	 */
	ret = asahi_g15c_source_packet(in, expected_chip, &result->packet);
	if (ret)
		goto free;
	ret = g15c_import_main(in->main, in->sram, in->gpu_num_perf_states,
			       in->table_count, &result->tables.main);
	if (ret)
		goto free;
	ret = g15c_import_aux(in->cs, 1, 16, &result->tables.cs);
	if (ret)
		goto free;
	ret = g15c_import_aux(in->afr, 1, 8, &result->tables.afr);
	if (ret)
		goto free;
	ret = g15c_import_shared_rail(&result->tables.cs, &result->tables.afr);
	if (ret)
		goto free;
	/* The main cap is indexed in the SELECTED primary table. */
	ret = g15c_native_caps(selector, revision,
			      (result->packet.length - 64) / 12,
			      result->tables.cs.count, result->tables.afr.count,
			      result->tables.caps);
	if (!ret)
		*out = *result;
free:
	kfree_sensitive(result);
	return ret;
}

int asahi_g15c_decode(const struct g15c_packed_coefficients *in,
		     struct g15c_coefficients *out)
{
	struct g15c_packed_coefficients input;
	struct g15c_coefficients result;
	int ret;

	if (!in || !out)
		return G15C_INVALID;
	ret = context_allowed();
	if (ret)
		return ret;
	input = *in;
	ret = owned_model(&input, &result, true);
	if (!ret)
		*out = result;
	return ret;
}

static void cell(struct g15c_board_packet *out, u32 index, u32 word)
{
	u8 *p = out->bytes + index * 4;

	p[0] = word >> 24;
	p[1] = word >> 16;
	p[2] = word >> 8;
	p[3] = word;
}

int asahi_g15c_board_packet(const struct g15c_board_packet_input *in,
			   u32 expected_chip, struct g15c_board_packet *out)
{
	struct packet_work *work;
	u32 count, state, mask, word;
	int ret;

	if (!in || !out || !expected_chip || in->chip_id != expected_chip ||
	    in->source.contract != G15C_23J220_CONTRACT || in->source.lanes != 4 ||
	    in->source.main_states < 1 || in->source.main_states > G15C_BOARD_RAW_STATES)
		return G15C_INVALID;
	if (in->masks_present != 7)
		return G15C_MISSING;
	ret = context_allowed();
	if (ret)
		return ret;
	work = kzalloc(sizeof(*work), GFP_KERNEL);
	if (!work)
		return -ENOMEM;
	ret = g15c_native_select(in->map_banks, in->source.main_states, &work->selection);
	if (ret)
		goto free;
	count = work->selection.primary_count;
	ret = G15C_INVALID;
	if (count < 2 || count != work->selection.secondary_count)
		goto free;
	work->selected = in->source;
	work->selected.main_states = count;
	for (state = 0; state < count; state++) {
		/* Keep both native streams; materialize only in-bounds rows. */
		if (work->selection.primary[state] >= in->source.main_states ||
		    work->selection.secondary[state] >= in->source.main_states)
			goto free;
		work->selected.main[state] = in->source.main[work->selection.primary[state]];
	}
	ret = owned_model(&work->selected, &work->power, false);
	if (ret)
		goto free;
	if (!work->power.reference_word) {
		ret = G15C_NUMERIC;
		goto free;
	}
	cell(&work->packet, 0, G15C_23J220_CONTRACT);
	cell(&work->packet, 1, in->chip_id);
	cell(&work->packet, 2, work->power.reference_word);
	cell(&work->packet, 3, count);
	for (mask = 0; mask < 3; mask++)
		for (word = 0; word < 4; word++)
			cell(&work->packet, 4 + mask * 4 + word, in->masks_msb[mask][word]);
	for (state = 0; state < count; state++) {
		cell(&work->packet, 16 + state, work->selection.primary[state]);
		cell(&work->packet, 16 + count + state, work->selection.secondary[state]);
		cell(&work->packet, 16 + 2 * count + state,
		     work->power.relative_percent_u32[state]);
	}
	work->packet.length = (16 + 3 * count) * 4;
	*out = work->packet;
free:
	/* No intermediate per-device coefficients or model data are retained. */
	kfree_sensitive(work);
	return ret;
}

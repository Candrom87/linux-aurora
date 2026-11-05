/* SPDX-License-Identifier: MIT */
#include "support.h"
#include "native.h"
#include "board_source.h"
unsigned int test_flags, test_preempt, test_owner, test_begin, test_end;
unsigned int test_alloc, test_free, test_probe_fail;
unsigned int test_fault;
int g15c_actual_ieee_probes(void);
int g15c_port_ieee_probes(void)
{
	assert(test_owner && test_preempt == 2);
	return !test_probe_fail && g15c_actual_ieee_probes();
}

/* Keep nondefault CPU FP controls inside a single integer-ABI C invocation;
 * never expose them to Python, libc numerical code or another process.
 */
int fixture_call(unsigned int flags, u64 fpcr, u64 fpsr, unsigned int decode,
		 const void *in, u32 chip, void *out)
{
	u64 saved_fpcr = read_sysreg_s(SYS_FPCR);
	u64 saved_fpsr = read_sysreg_s(SYS_FPSR);
	int ret;

	test_flags = flags; test_owner = !!(flags & 8); test_preempt = test_fault = 0;
	test_alloc = test_free = test_begin = test_end = 0;
	test_probe_fail = !!(flags & 256);
	write_sysreg_s(fpcr, SYS_FPCR); write_sysreg_s(fpsr, SYS_FPSR);
	/* Use readback rather than assuming optional control bits are implemented. */
	fpcr = read_sysreg_s(SYS_FPCR); fpsr = read_sysreg_s(SYS_FPSR);
	if (decode == 3)
		ret = asahi_g15c_source_bundle(in, chip, out);
	else if (decode == 2)
		ret = asahi_g15c_source_packet(in, chip, out);
	else
		ret = decode ? asahi_g15c_decode(in, out) : asahi_g15c_board_packet(in, chip, out);
	bool controls = fpcr == read_sysreg_s(SYS_FPCR) && fpsr == read_sysreg_s(SYS_FPSR);
	write_sysreg_s(saved_fpsr, SYS_FPSR); write_sysreg_s(saved_fpcr, SYS_FPCR);
	assert(controls);
	assert(!test_preempt && test_owner == !!(flags & 8));
	assert(test_begin == test_end && test_alloc == test_free);
	if (flags & (1 | 2 | 4 | 8 | 16 | 32 | 64))
		assert(!test_begin);
	return test_fault ? -4095 : ret;
}

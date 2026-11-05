/* SPDX-License-Identifier: MIT */
/* CPU-only fixture services. Kernel scheduling/FPSIMD ownership is MODELED;
 * FPCR/FPSR reads and writes execute in this ordinary userspace process.
 */
#ifndef G15C_TEST_SUPPORT_H
#define G15C_TEST_SUPPORT_H
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint64_t u64;
typedef uint32_t u32;
typedef uint8_t u8;
extern unsigned int test_flags, test_preempt, test_owner, test_begin, test_end;
extern unsigned int test_alloc, test_free, test_probe_fail;
extern unsigned int test_fault;
/* Faulted variants return a distinguishable failure without generating host
 * core dumps or changing a device/kernel. Continue only in this CPU fixture.
 */
#undef assert
#define assert(x) do { if (!(x)) test_fault++; } while (0)
#define TIF_KERNEL_FPSTATE 0
#define GFP_KERNEL 0
static inline bool in_task(void) { return !(test_flags & 1); }
static inline bool irqs_disabled(void) { return test_flags & 2; }
static inline bool in_atomic(void) { return test_flags & 4; }
static inline bool test_thread_flag(int f) { (void)f; return test_owner; }
static inline bool system_capabilities_finalized(void) { return !(test_flags & 16); }
static inline bool kernel_fpu_available(void) { return !(test_flags & 32); }
static inline bool may_use_simd(void) { return !(test_flags & 64); }
static inline void preempt_disable(void) { test_preempt++; }
static inline void preempt_enable(void) { assert(test_preempt); test_preempt--; }
static inline void kernel_fpu_begin(void)
{
	assert(test_preempt && !test_owner);
	preempt_disable(); test_owner = 1; test_begin++;
}
static inline void kernel_fpu_end(void)
{
	assert(test_preempt == 2 && test_owner);
	test_owner = 0; test_end++; preempt_enable();
}
static inline void *kzalloc(size_t n, int flags)
{
	(void)flags;
	assert(!test_preempt && !test_owner);
	if ((test_flags & 128) || ((test_flags & 512) && test_alloc == 1)) return NULL;
	void *p = calloc(1, n);
	if (p) test_alloc++;
	return p;
}
static inline void kfree_sensitive(void *p)
{
	assert(!test_preempt && !test_owner && p);
	test_free++; free(p);
}
#define SYS_FPCR S3_3_C4_C4_0
#define SYS_FPSR S3_3_C4_C4_1
#define G15C_STRING_(x) #x
#define G15C_STRING(x) G15C_STRING_(x)
#define read_sysreg_s(r) ({ u64 v; __asm__ volatile("mrs %0, " G15C_STRING(r) : "=r"(v) :: "memory"); v; })
#define write_sysreg_s(v, r) do { u64 value_ = (v); __asm__ volatile("msr " G15C_STRING(r) ", %0" :: "r"(value_) : "memory"); } while (0)
#endif

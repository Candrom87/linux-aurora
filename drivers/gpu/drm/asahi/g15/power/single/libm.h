/* SPDX-License-Identifier: MIT */
/* Minimal freestanding helpers used by the unchanged m1n1 Arm powf source.
 * The unused long-double and hosted-library interfaces are deliberately absent.
 */
#ifndef ASAHI_G15C_SINGLE_LIBM_H
#define ASAHI_G15C_SINGLE_LIBM_H
#include <stdint.h>
#include <math.h>
#define WANT_ROUNDING 1
#define WANT_SNAN 0
#define WANT_ERRNO 0
#define WANT_ERRNO_UFLOW 0
#define TOINT_INTRINSICS 0
#define issignalingf_inline(x) 0
#define predict_true(x) __builtin_expect(!!(x), 1)
#define predict_false(x) __builtin_expect(!!(x), 0)
float __math_invalidf(float);
static inline float eval_as_float(float x) { return x; }
static inline double eval_as_double(double x) { return x; }
static inline float fp_barrierf(float x) { volatile float y = x; return y; }
static inline void fp_force_evalf(float x) { volatile float y = x; (void)y; }
#define asuint(x) (((union { float f; uint32_t u; }){ .f = (x) }).u)
#define asfloat(x) (((union { uint32_t u; float f; }){ .u = (x) }).f)
#define asuint64(x) (((union { double f; uint64_t u; }){ .f = (x) }).u)
#define asdouble(x) (((union { uint64_t u; double f; }){ .u = (x) }).f)
static inline float __math_xflowf(uint32_t sign, float y)
{
	return eval_as_float(fp_barrierf(sign ? -y : y) * y);
}
static inline float __math_oflowf(uint32_t sign)
{
	return __math_xflowf(sign, 0x1p97f);
}
static inline float __math_uflowf(uint32_t sign)
{
	return __math_xflowf(sign, 0x1p-95f);
}
#endif

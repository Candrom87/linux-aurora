/* SPDX-License-Identifier: MIT */
#ifndef ASAHI_G15C_MATH_H
#define ASAHI_G15C_MATH_H
/* No hosted libm symbols. Explicit FMA inside Arm pow is intentional; model
 * operations remain separately rounded with -ffp-contract=off.
 */
double g15c_arm_pow64(double, double);
float g15c_arm_pow32(float, float);
#define pow g15c_arm_pow64
#define powf g15c_arm_pow32
#define fminf(x, y) __builtin_fminf((x), (y))
#define fma(x, y, z) __builtin_fma((x), (y), (z))
#define fabs(x) __builtin_fabs(x)
#define round(x) __builtin_round(x)
#define lround(x) __builtin_lround(x)
#define isfinite(x) __builtin_isfinite(x)
#define isnan(x) __builtin_isnan(x)
#define isinf(x) __builtin_isinf(x)
#ifndef NAN
#define NAN __builtin_nanf("")
#endif
#ifndef INFINITY
#define INFINITY __builtin_inff()
#endif
#endif

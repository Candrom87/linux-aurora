/* SPDX-License-Identifier: MIT */
/* The kernel's function-like attribute macro expands inside Arm's literal
 * __attribute__((noinline)). These private FP units do not use that macro.
 */
#undef noinline

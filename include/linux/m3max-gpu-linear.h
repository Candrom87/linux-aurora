/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef M3MAX_GPU_LINEAR_H
#define M3MAX_GPU_LINEAR_H
#include <linux/init.h>
#include <linux/types.h>
#ifdef CONFIG_M3MAX_GPU_LINEAR_MEMORY
void __init m3max_gpu_linear_init(phys_addr_t memory_limit);
#else
static inline void m3max_gpu_linear_init(phys_addr_t memory_limit) { }
#endif
#endif

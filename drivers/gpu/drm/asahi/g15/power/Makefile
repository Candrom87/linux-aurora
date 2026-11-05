# SPDX-License-Identifier: GPL-2.0-only OR MIT
# Explicit-input arithmetic only. No T6034 match, caller or device operation.
g15c-fp := g15c_23j220_power.o ieee_probes.o arm/pow.o arm/pow_log_data.o \
	arm/exp_data.o arm/math_err.o single/powf.o single/powf_data.o single/exp2f_data.o
g15c-int := kernel.o guard.o fpcr_read.o native_select.o native_import.o board_source.o
g15c-flags := -I$(src)/g15/power/include -I$(src)/g15/power \
	-fno-fast-math -ffp-contract=off -fno-math-errno -fno-builtin
g15c-core-flags := -Dg15c_23j220_decode=g15c_core_decode \
	-Dg15c_23j220_cs_word=g15c_core_cs_word \
	-Dg15c_23j220_main_word=g15c_core_main_word \
	-Dg15c_23j220_produce=g15c_core_produce
g15c-arm-flags := -DUSE_GLIBC_ABI=0 -DWANT_ERRNO=0 -DWANT_ERRNO_UFLOW=0 \
	-DWANT_ROUNDING=1 -DWANT_EXPERIMENTAL_MATH=0 -DHAVE_FAST_FMA=1 \
	-I$(src)/g15/power/arm/include -include $(src)/g15/power/arm/compat.h
$(foreach o,$(g15c-fp),$(eval CFLAGS_g15/power/$(o) += $(g15c-flags) $(CC_FLAGS_FPU)))
$(foreach o,$(g15c-int),$(eval CFLAGS_g15/power/$(o) += $(g15c-flags)))
$(foreach o,$(g15c-fp),$(eval CFLAGS_REMOVE_g15/power/$(o) += $(CC_FLAGS_NO_FPU) $(CC_FLAGS_LTO)))
$(foreach o,$(g15c-int),$(eval CFLAGS_REMOVE_g15/power/$(o) += $(CC_FLAGS_LTO)))
$(foreach o,$(filter arm/%,$(g15c-fp)),$(eval CFLAGS_g15/power/$(o) += $(g15c-arm-flags)))
$(foreach o,$(filter single/%,$(g15c-fp)),$(eval CFLAGS_g15/power/$(o) += -include $(src)/g15/power/single/namespace.h))
CFLAGS_g15/power/g15c_23j220_power.o += $(g15c-core-flags)
CFLAGS_g15/power/ieee_probes.o += -include $(src)/g15/power/port.h
CFLAGS_g15/power/fpcr_read.o += -include $(src)/g15/power/port.h
CFLAGS_g15/power/board_source.o += -Dg15c_23j220_decode=asahi_g15c_decode \
	-include $(src)/g15/power/arm/compat.h
asahi-$(CONFIG_DRM_ASAHI_G15_NATIVE_POWER) += $(addprefix g15/power/,$(g15c-fp) $(g15c-int))

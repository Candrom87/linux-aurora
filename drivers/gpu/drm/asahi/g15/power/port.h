/* SPDX-License-Identifier: MIT */
#ifndef ASAHI_G15C_PORT_H
#define ASAHI_G15C_PORT_H
#include "g15c_23j220_power.h"
uint64_t g15c_port_read_fpcr(void);
int g15c_port_fpcr_accepted(uint64_t);
int g15c_port_ieee_environment(void);
int g15c_port_ieee_probes(void);
int g15c_core_decode(const struct g15c_packed_coefficients *, struct g15c_coefficients *);
int g15c_core_cs_word(uint32_t, uint32_t, uint32_t, uint32_t, uint32_t *);
int g15c_core_main_word(uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, uint32_t *);
int g15c_core_produce(const struct g15c_power_input *, struct g15c_power_output *);
#endif

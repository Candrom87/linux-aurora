/* SPDX-License-Identifier: MIT */
#ifndef ASAHI_G15C_FENV_H
#define ASAHI_G15C_FENV_H
int g15c_port_ieee_environment(void);
#define FE_TONEAREST 0
#define fegetround() (g15c_port_ieee_environment() ? FE_TONEAREST : -1)
#endif

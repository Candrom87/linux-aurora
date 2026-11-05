// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Original implementation of the bounded 23J220 G15C cap getter contract. */
#include "native.h"

int g15c_native_caps(uint32_t selector, uint32_t revision,
		     uint32_t main, uint32_t cs, uint32_t afr, uint32_t caps[3])
{
	uint32_t result[3];

	if (!caps || selector != 0x18 || main < 2 || main > 16 ||
	    cs < 2 || cs > 16 || afr < 2 || afr > 8)
		return G15C_INVALID;
	result[0] = revision == 1 ? 7 : main - 1;
	result[1] = revision == 1 ? 5 : cs - 1;
	result[2] = revision == 1 ? 5 : afr - 1;
	if (result[0] >= main || result[1] >= cs || result[2] >= afr)
		return G15C_INVALID;
	for (uint32_t i = 0; i < 3; i++)
		caps[i] = result[i];
	return G15C_OK;
}

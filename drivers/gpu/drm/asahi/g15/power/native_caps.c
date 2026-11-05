// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Original implementation of the bounded 23J220 G15C cap getter contract. */
#include "native.h"

int g15c_native_context(const uint32_t identity[6], struct g15c_cap_context *out)
{
	struct g15c_cap_context result = { .selector = 0x18 };
	uint32_t major, minor;

	if (!identity || !out || identity[0] >> 24 != 7 ||
	    ((identity[0] >> 16) & 255) != 4 || ((identity[1] >> 16) & 15) != 1 ||
	    ((identity[1] >> 8) & 255) != 4 || (identity[1] & 255) != 10)
		return G15C_INVALID;
	major = (identity[0] >> 12) & 15;
	minor = (identity[0] >> 8) & 15;
	if (!major && !minor)
		result.revision = 1;
	else if (major == 1 && !minor)
		result.revision = 3;
	else if (major == 1 && minor == 1)
		result.revision = 4;
	else if (major == 2 && !minor)
		result.revision = 5;
	/* Other encodings leave +4 as the native probe initialized it: zero. */
	*out = result;
	return G15C_OK;
}

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

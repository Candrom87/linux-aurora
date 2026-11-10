/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef M3MAX_NATIVE_CLIENT_H
#define M3MAX_NATIVE_CLIENT_H
#include "native_efi_bridge.h"
efi_status_t m3max_native_start(void *handle, efi_loaded_image_t *parent,
			      void *caller_context, ne_continue_fn continuation,
			      unsigned int profile);
#endif

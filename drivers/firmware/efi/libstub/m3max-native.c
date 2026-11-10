// SPDX-License-Identifier: GPL-2.0-only
/* Start the reviewed, embedded native EFI application. The application calls
 * back while its StartImage frame remains live. Successful full boot never
 * returns here, nor unloads the application after ExitBootServices. */
#ifdef M3MAX_EFI_FIXTURE
#include "native_efi_fixture_kernel.h"
#else
#include "efistub.h"
#endif
#include "m3max-native.h"

extern const unsigned char m3max_native_bundle_start[];
extern const unsigned char m3max_native_bundle_end[];

efi_status_t m3max_native_start(void *handle, efi_loaded_image_t *parent,
			      void *caller_context, ne_continue_fn continuation,
			      unsigned int profile)
{
	efi_guid_t guid = LOADED_IMAGE_PROTOCOL_GUID;
	efi_loaded_image_t *child = NULL;
	efi_handle_t child_handle = NULL;
	unsigned long bytes = m3max_native_bundle_end - m3max_native_bundle_start;
	struct ne_launch launch = { 0 };
	efi_status_t status;

	if (!parent || !ne_range((u64)parent->image_base, parent->image_size) ||
	    !handle || !caller_context || !continuation || !bytes || bytes > NE_IMAGE_MAX ||
	    (profile != NE_PROFILE_FULL && profile != NE_PROFILE_METADATA))
		return EFI_INVALID_PARAMETER;
	status = efi_bs_call(load_image, false, handle, NULL,
			     (void *)m3max_native_bundle_start, bytes, &child_handle);
	if (status != EFI_SUCCESS)
		return status;
	status = efi_bs_call(handle_protocol, child_handle, &guid, (void **)&child);
	if (status != EFI_SUCCESS || !child || child->load_options || child->load_options_size ||
	    child->parent_handle != handle) {
		status = EFI_LOAD_ERROR;
		goto unload;
	}
	launch = (struct ne_launch){
		.magic = NE_MAGIC, .version = NE_VERSION, .bytes = sizeof(launch),
		.profile = profile,
		.required_caps = profile == NE_PROFILE_FULL ? NE_FULL_CAPS : NE_CAP_METADATA,
		.parent_handle = handle, .child_handle = child_handle,
		.parent_base = (u64)parent->image_base, .parent_bytes = parent->image_size,
		.child_base = (u64)child->image_base, .child_bytes = child->image_size,
		.caller_context = caller_context, .continue_boot = continuation,
	};
	if (!ne_launch_valid(&launch)) {
		status = EFI_LOAD_ERROR;
		goto unload;
	}
	child->load_options = &launch;
	child->load_options_size = sizeof(launch);
	status = efi_bs_call(start_image, child_handle, NULL, NULL);
	/* An EFI application is unloaded on return/Exit. Do not dereference child
	 * or unload the stale handle here. Full success must enter Linux; a
	 * returned EFI_SUCCESS is an incomplete boot and must remain an error. */
	return status == EFI_SUCCESS ? EFI_ABORTED : status;

unload:
	if (efi_bs_call(unload_image, child_handle) != EFI_SUCCESS)
		return EFI_ABORTED;
	return status;
}

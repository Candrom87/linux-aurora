// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! Staged 23J220 thermal selections from one source-bound membership.
//! The firmware token scopes the software contract only. Supplied identity
//! words still require power-owned fresh acquisition; this helper attests
//! neither that provenance nor physical thermal safety. No device I/O here.

use core::marker::PhantomData;
use crate::g15::{adt::native_mtr::Membership, identity::NativeFirmware};
use super::super::record::NATIVE_CONTRACT;

type R<T> = Result<T, &'static str>;

/// Independent fast-die, SoCHot and enabled/alarm cells, in that order.
/// Keeps the source membership, software token and supplied identity borrowed.
pub(crate) struct Masks<'a> {
    cells: [[u32; 4]; 3],
    source: PhantomData<(&'a Membership<'a>, &'a NativeFirmware, &'a [u32; 6])>,
}

impl Masks<'_> {
    pub(crate) fn cells(&self) -> &[[u32; 4]; 3] {
        &self.cells
    }
}

pub(crate) fn select<'a>(firmware: &'a NativeFirmware, membership: &'a Membership<'a>,
                         identity: &'a [u32; 6]) -> R<Masks<'a>> {
    if firmware.build().native_board_contract != Some(NATIVE_CONTRACT) {
        return Err("native thermal firmware contract");
    }
    #[cfg(CONFIG_DRM_ASAHI_G15_NATIVE_POWER)]
    {
        extern "C" {
            fn g15c_native_thermal_masks(identity: *const u32, enabled: *const u32,
                                         output: *mut [[u32; 4]; 3]) -> core::ffi::c_int;
        }
        let enabled = membership.limbs_msb();
        let mut cells = [[0; 4]; 3];
        // SAFETY: fixed-size integer input/output arrays remain live and
        // disjoint. C uses no FPU, allocation or kernel/device service.
        let status = unsafe {
            g15c_native_thermal_masks(identity.as_ptr(), enabled.as_ptr(), &mut cells)
        };
        if status != 0 {
            return Err("native thermal identity or membership");
        }
        Ok(Masks { cells, source: PhantomData })
    }
    #[cfg(not(CONFIG_DRM_ASAHI_G15_NATIVE_POWER))]
    {
        let _ = (membership, identity);
        Err("native thermal producer unavailable")
    }
}

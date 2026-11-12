// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! Exact firmware and hardware identity.
//!
//! The firmware build is the SHA-256 of TEXT with the 8 entropy bytes after the single
//! `GKTS\x08\0\0\0` marker zeroed, plus the segment sizes, looked up in the SoC
//! configuration's `firmware` list; the hardware revision is checked against its `hw_ids` at
//! start ([`super::boot`]). An unknown build or revision is refused with its measured identity;
//! an unknown build's refusal also says what to report so that it can be pinned. The pure
//! lookup is in [`lookup`] (host-tested).
//! The canonical object VAs, the initdata graph, the board images and the work layouts all
//! belong to one build, so nothing is written before the build is identified.
//!
//! Bring-up escape hatch: `g15_accept_text_prefix=<hex>` admits an unpinned build whose digest
//! starts with those 8 bytes (the full digest and marker offset are logged, so that the build
//! can be pinned by the next kernel).
//! That legacy escape never qualifies native board-data production: native
//! admission requires an exact board-restricted image with an explicit matching
//! producer contract and returns a separate sealed software-identity token.

use kernel::{bindings, c_str, device, fmt, prelude::*};

use super::boot::glue;
use super::hw::FirmwareBuild;

mod lookup;
mod matching;

use lookup::{Measured, Verdict};

/// Lowercase hex of a byte string, as `%*phN` prints it.
pub(crate) struct Hex<'a>(pub(crate) &'a [u8]);

impl fmt::Display for Hex<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        for b in self.0 {
            write!(f, "{:02x}", b)?;
        }
        Ok(())
    }
}

/// Copies a caller-qualified boot-memory range into owned bytes through a temporary
/// write-back mapping. Callers establish the range's ownership and compatible memory
/// type first; the native ADT path requires wholly reserved RAM.
pub(crate) fn copy_physical(phys: u64, size: usize) -> Result<KVVec<u8>> {
    // SAFETY: memremap() of a boot-loader range creates a temporary mapping; it has no other
    // side effects.
    let map = unsafe { bindings::memremap(phys, size, bindings::MEMREMAP_WB as _) };
    if map.is_null() {
        return Err(ENOMEM);
    }
    let copy = KVVec::from_elem(0u8, size, GFP_KERNEL).map(|mut copy| {
        // SAFETY: `map` is a live mapping of `size` bytes and `copy` holds `size` bytes; the two do
        // not overlap.
        unsafe { core::ptr::copy_nonoverlapping(map as *const u8, copy.as_mut_ptr(), size) };
        copy
    });
    // SAFETY: `map` came from memremap() above and is not used afterwards.
    unsafe { bindings::memunmap(map) };
    Ok(copy?)
}

impl From<&FirmwareBuild> for Measured {
    fn from(build: &FirmwareBuild) -> Self {
        Measured {
            text_size: build.text_size,
            data_size: build.data_size,
            marker: build.marker,
            sha256: build.text_sha256,
        }
    }
}

/// Identifies the loaded firmware build: the pinned build, or `None` for a build admitted by
/// its digest prefix (`g15_accept_text_prefix`).
pub(crate) fn firmware(
    dev: &device::Device,
    adt: &super::adt::GpuAdt,
    builds: &'static [FirmwareBuild],
    allow_prefix: bool,
) -> Result<Option<&'static FirmwareBuild>> {
    let prefix = if !allow_prefix || builds.is_empty() { 0 } else {
        super::params::accept_text_prefix()
    };
    firmware_checked(dev, adt, builds, prefix, None)
}

/// An exact board-restricted image matched to the native board-data contract.
/// Binds that software to the original device allocation and copied source,
/// not GPU state, power, fresh register words or register/memory handoff.
/// Production construction is restricted to native_firmware. The KUnit-only
/// synthetic constructor below is not firmware measurement or admission.
pub(crate) struct NativeFirmware {
    build: &'static FirmwareBuild,
    source: super::adt::source::Source,
    dev: kernel::sync::aref::ARef<device::Device>,
}

/// Synthetic software-context service for the marked metadata fixture only.
/// No loaded firmware is measured or admitted, and this descriptor is absent
/// from every production profile. Used to test source/device binding and the
/// typed producer/image path with invented input data.
#[cfg(CONFIG_DRM_ASAHI_G15_BOARD_KUNIT_TEST)]
pub(crate) fn fixture_native_firmware(
    dev: &device::Device, adt: &super::adt::GpuAdt,
) -> Result<NativeFirmware> {
    static BUILD: FirmwareBuild = FirmwareBuild {
        name: "Synthetic KUnit context; no firmware measurement",
        machine: Some(kernel::c_str!("apple,j514m")),
        native_board_contract: Some(super::board::record::NATIVE_CONTRACT),
        text_size: 0x4000, data_size: 0x4000, marker: 32, text_sha256: [0; 32],
    };
    let snapshot = adt.native.as_ref().ok_or(EINVAL)?;
    snapshot.check_firmware_regions(0x6034, &adt.text, &adt.data)?;
    Ok(NativeFirmware { build: &BUILD, source: snapshot.source_identity().clone(), dev: dev.into() })
}

impl NativeFirmware {
    pub(crate) fn build(&self) -> &'static FirmwareBuild {
        self.build
    }

    pub(crate) fn matches_source(&self, snapshot: &super::adt::NativeAdt) -> bool {
        self.source.same(snapshot.source_identity())
    }

    /// Pins/identifies the original allocation and copied source, not the
    /// current binding generation, probe lock, power or physical handoff.
    pub(crate) fn matches(&self, dev: &device::Device, snapshot: &super::adt::NativeAdt) -> bool {
        self.dev.as_raw() == dev.as_raw() && self.matches_source(snapshot)
    }
}

/// Native board production must never inherit the legacy digest-prefix escape
/// or silently use a pinned image qualified only for legacy templates/records.
pub(crate) fn native_firmware(
    dev: &device::Device,
    adt: &super::adt::GpuAdt,
    builds: &'static [FirmwareBuild],
    chip: u32,
) -> Result<NativeFirmware> {
    if chip != 0x6034 || !glue::machine(c_str!("apple,j514m")) {
        return Err(ENODEV);
    }
    let snapshot = adt.native.as_ref().ok_or(ENODEV)?;
    snapshot.check_firmware_regions(chip, &adt.text, &adt.data)?;
    let build = firmware_checked(
        dev,
        adt,
        builds,
        0,
        Some(super::board::record::NATIVE_CONTRACT),
    )?
    .ok_or(ENODEV)?;
    Ok(NativeFirmware { build, source: snapshot.source_identity().clone(), dev: dev.into() })
}

fn firmware_checked(
    dev: &device::Device,
    adt: &super::adt::GpuAdt,
    builds: &'static [FirmwareBuild],
    accept_prefix: u64,
    native_contract: Option<u32>,
) -> Result<Option<&'static FirmwareBuild>> {
    let text_size = adt.text.end.checked_sub(adt.text.start)
        .filter(|&size| size != 0).ok_or(EINVAL)?;
    let data_size = adt.data.end.checked_sub(adt.data.start)
        .filter(|&size| size != 0).ok_or(EINVAL)?;
    let qualifies = |b: &FirmwareBuild| {
        b.machine.map_or(true, glue::machine)
            && native_contract.map_or(true, |contract| {
                b.machine == Some(c_str!("apple,j514m"))
                    && b.native_board_contract == Some(contract)
            })
    };
    // Native admission has no prefix escape: without an eligible descriptor
    // of this geometry there is no possible success after a TEXT copy. Check
    // that before mapping boot memory. A candidate still needs the complete
    // measured identity below; this is not an image or memory qualification.
    // Legacy diagnostics keep measuring unknown geometry for their prefix path.
    if native_contract.is_some() && !builds.iter().any(|b| {
        qualifies(b) && b.text_size == text_size && b.data_size == data_size
            && b.marker % 8 == 0 && u64::from(b.marker) + 16 <= text_size
    }) {
        dev_err!(dev, "no native GPU firmware descriptor for board/contract and TEXT {:#x} DATA {:#x}\n",
                 text_size, data_size);
        return Err(EINVAL);
    }
    let size = usize::try_from(text_size)?;
    let mut copy = copy_physical(adt.text.start, size)?;
    let at = lookup::marker(&copy).map_err(|found| {
        dev_err!(
            dev,
            "GPU firmware TEXT has {} GKTS markers, need one\n",
            found
        );
        EINVAL
    })?;
    copy[at + 8..at + 16].fill(0);
    let m = Measured {
        text_size: size as u64,
        data_size,
        marker: u32::try_from(at)?,
        sha256: glue::sha256(&copy),
    };
    // A qualified build pins only on the machine it names.
    let qualified = builds.iter().map(|b| (Measured::from(b), qualifies(b)));
    match lookup::verdict(&m, qualified, accept_prefix) {
        Verdict::Pinned(i) => {
            let build = builds.get(i).ok_or(EINVAL)?;
            dev_info!(dev, "GPU firmware build: {}\n", build.name);
            return Ok(Some(build));
        }
        Verdict::Admitted => {
            dev_warn!(
                dev,
                "GPU firmware build admitted by g15_accept_text_prefix: TEXT {:#x} DATA {:#x} GKTS {:#x} sha256 {}\n",
                size,
                data_size,
                at,
                Hex(&m.sha256)
            );
            return Ok(None);
        }
        Verdict::Refused => {}
    }
    dev_err!(
        dev,
        "unsupported GPU firmware build: TEXT {:#x} DATA {:#x} GKTS {:#x} sha256 {}; {}\n",
        size,
        data_size,
        at,
        Hex(&m.sha256),
        lookup::REPORT_HINT
    );
    for build in builds {
        dev_err!(dev, "supported: {}\n", build.name);
    }
    Err(EINVAL)
}

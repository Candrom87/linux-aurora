// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! Owned C output and its single checked borrowed construction view.
//! This binds packet/tables together, not source/firmware/power provenance.

use core::marker::PhantomData;
use super::record::{self, NativeTables, Reference};

pub(crate) mod thermal;

type R<T> = Result<T, &'static str>;

/// The model's -1/-2/-3 namespace is not Linux EPERM/ENOENT/ESRCH.
/// Preserve the wrapper's real context/allocation errno values.
pub(super) fn linux_status(status: i32) -> i32 {
    match status { -3..=-1 => -22, value => value }
}

/// Supplied by the future power-owned register acquisition. No method here
/// reads registers or treats caller-supplied words as an attestation.
pub(crate) struct Words {
    pub(crate) identity: [u32; 6],
    pub(crate) coefficients: [u32; 4],
    pub(crate) coefficients_present: u32,
    pub(crate) map_banks: [[u32; 8]; 2],
    pub(crate) cs_activity_mask: u32,
}

#[repr(C)]
#[derive(Copy, Clone)]
struct Property {
    bytes: *const u8,
    length: usize,
}

impl Property {
    fn new(value: Option<&[u8]>) -> Self {
        value.map_or(Self { bytes: core::ptr::null(), length: 0 }, |value| Self {
            bytes: value.as_ptr(), length: value.len(),
        })
    }
}

#[repr(C)]
struct Coefficients {
    words: [u32; 4],
    present: u32,
}

#[repr(C)]
pub(super) struct Input<'a> {
    contract: u32,
    chip: u32,
    gpu_num_perf_states: u32,
    table_count: u32,
    main: Property,
    sram: Property,
    cs: Property,
    afr: Property,
    identity: [u32; 6],
    coefficients: Coefficients,
    map_banks: [[u32; 8]; 2],
    cs_activity_mask: u32,
    masks_msb: [[u32; 4]; 3],
    masks_present: u32,
    borrowed: PhantomData<&'a [u8]>,
}

impl<'a> Input<'a> {
    pub(super) fn from_native(snapshot: &'a crate::g15::adt::NativeAdt,
                             firmware: &crate::g15::identity::NativeFirmware,
                             words: &Words, chip: u32) -> R<Self> {
        if !firmware.matches_source(snapshot) {
            return Err("native board firmware/source association");
        }
        // Both views come from the same immutable owner, never caller-spliced
        // property slices or a historical/manual sensor mask tuple.
        let properties = snapshot.properties(chip).map_err(|_| "native board properties")?;
        let membership = snapshot.mtr_membership(chip).map_err(|_| "native board membership")?;
        let masks = thermal::select(firmware, &membership, &words.identity)?;
        Ok(Self {
            contract: record::NATIVE_CONTRACT, chip,
            gpu_num_perf_states: properties.gpu_num_perf_states,
            table_count: properties.table_count,
            main: Property::new(Some(properties.main)), sram: Property::new(properties.sram),
            cs: Property::new(Some(properties.cs)), afr: Property::new(Some(properties.afr)),
            identity: words.identity,
            coefficients: Coefficients { words: words.coefficients, present: words.coefficients_present },
            map_banks: words.map_banks, cs_activity_mask: words.cs_activity_mask,
            masks_msb: *masks.cells(), masks_present: 7,
            borrowed: PhantomData,
        })
    }
}

/// Exact C output, fully initialized only by a successful C call. The current
/// construction view borrows its immutable owner; no float fields cross ABI.
#[repr(C)]
pub(crate) struct Bundle {
    length: u32,
    packet: [u8; 256],
    tables: NativeTables,
}

const _: () = assert!(core::mem::size_of::<Bundle>() == 2024);
const _: () = assert!(core::mem::align_of::<Bundle>() == 4);
#[cfg(target_pointer_width = "64")]
const _: () = assert!(core::mem::size_of::<Input<'_>>() == 248);

pub(crate) struct Bound<'a> {
    reference: Reference,
    bundle: &'a Bundle,
}

impl<'a> Bound<'a> {
    pub(crate) fn new(bundle: &'a Bundle, chip: u32) -> R<Self> {
        let length = bundle.length as usize;
        if length > bundle.packet.len() || length % 4 != 0 {
            return Err("native bundle packet length");
        }
        let mut cells = [0u32; record::NATIVE_MAX_CELLS];
        for (cell, bytes) in cells.iter_mut().zip(bundle.packet[..length].chunks_exact(4)) {
            *cell = u32::from_be_bytes(bytes.try_into().map_err(|_| "native packet cell")?);
        }
        let reference = Reference::from_native_cells(chip, &cells[..length / 4])?;
        record::validate_native(&reference, &bundle.tables)?;
        Ok(Self { reference, bundle })
    }

    pub(crate) fn reference(&self) -> &Reference { &self.reference }
    pub(crate) fn tables(&self) -> &NativeTables { &self.bundle.tables }
}

/// Native reference and tables can only be provided through the same bundle.
pub(crate) enum Source<'a> {
    Legacy(&'a Reference),
    Native(Bound<'a>),
}

impl Source<'_> {
    pub(crate) fn reference(&self) -> &Reference {
        match self { Self::Legacy(reference) => reference, Self::Native(bound) => bound.reference() }
    }
    pub(crate) fn tables(&self) -> Option<&NativeTables> {
        match self { Self::Legacy(_) => None, Self::Native(bound) => Some(bound.tables()) }
    }
}

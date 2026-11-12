// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! Owned C output and its single checked borrowed construction view.
//! This binds packet/tables together, not source/firmware/power provenance.

use super::record::{self, NativeTables, Reference};

type R<T> = Result<T, &'static str>;

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

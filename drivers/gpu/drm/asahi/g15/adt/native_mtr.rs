// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! Enabled MTR sensor membership from one immutable native ADT copy.
//!
//! The 23J220 security-enabled host path consumes ID/byte-length/payload
//! records from PMGR's mtr-polynom-fuse-agx property. This parser skips every
//! polynomial payload; it neither interprets nor exports coefficients. Strict
//! shape and size limits are Linux admission policy, not claimed native checks.
//! Membership alone does not qualify runtime dispatch, firmware, thermal
//! configuration or safe hardware operation. No mapping or device I/O here.

use core::marker::PhantomData;
use super::native::{child, required};
use super::{le32, Adt};

type R<T> = Result<T, &'static str>;

/// Descriptive membership bound to its original copied source. Neither a
/// current register snapshot nor permission to use this as a hotspot mask.
pub(crate) struct Membership<'a> {
    mask: u128,
    records: usize,
    source: PhantomData<&'a [u8]>,
}

impl<'a> Membership<'a> {
    pub(super) fn new(data: &'a [u8], chip: u32) -> R<Self> {
        if chip != 0x6034 || data.len() > 0x20_0000 {
            return Err("native MTR chip or copy size");
        }
        let adt = Adt { data };
        adt.node_end(0, 0).ok_or("native MTR ADT structure")?;
        let armio = child(&adt, 0, b"arm-io\0")?;
        let sgx = child(&adt, armio, b"sgx\0")?;
        let pmgr = child(&adt, armio, b"pmgr\0")?;
        if required(&adt, sgx, b"compatible")? != b"gpu,t6034\0" ||
            required(&adt, pmgr, b"compatible")? != b"pmgr1,t6031\0"
        {
            return Err("native MTR source selection");
        }
        let raw = required(&adt, pmgr, b"mtr-polynom-fuse-agx")?;
        if raw.is_empty() || raw.len() & 3 != 0 || raw.len() > 0x10_0000 {
            return Err("native MTR property size");
        }
        let mut offset = 0;
        let mut mask = 0u128;
        let mut records = 0;
        while offset < raw.len() {
            if raw.len() - offset < 8 {
                return Err("native MTR truncated header");
            }
            let id = le32(raw, offset).ok_or("native MTR sensor ID")?;
            let size = le32(raw, offset + 4).ok_or("native MTR payload size")? as usize;
            if id >= 128 || size & 3 != 0 || size > raw.len() - offset - 8 {
                return Err("native MTR record shape");
            }
            mask |= 1u128 << id;
            records += 1;
            offset += 8 + size;
        }
        Ok(Self { mask, records, source: PhantomData })
    }

    /// Independent enabled/alarm membership, in native transport cell order.
    /// Selected fast-die and SoCHot masks still need their own construction.
    pub(crate) fn limbs_msb(&self) -> [u32; 4] {
        [(self.mask >> 96) as u32, (self.mask >> 64) as u32,
         (self.mask >> 32) as u32, self.mask as u32]
    }

    pub(crate) fn records(&self) -> usize {
        self.records
    }

    pub(crate) fn unique_ids(&self) -> u32 {
        self.mask.count_ones()
    }
}

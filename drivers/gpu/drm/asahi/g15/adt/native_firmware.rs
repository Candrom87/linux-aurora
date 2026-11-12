// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! Native segment metadata from the same owned source as board inputs.
//! No mapping, physical permission, ownership or firmware-state attestation.

use core::ops::Range;
use super::{Adt, le32, le64};
use super::native::{child, required, u32_property};
use super::super::hw;

type R<T> = Result<T, &'static str>;

pub(super) fn validate(bytes: &[u8], chip: u32, text: &Range<u64>,
                       data: &Range<u64>) -> R<()> {
    if chip != 0x6034 || bytes.len() > 0x20_0000 {
        return Err("native firmware source chip or size");
    }
    let adt = Adt { data: bytes };
    adt.node_end(0, 0).ok_or("native firmware source structure")?;
    let chosen = child(&adt, 0, b"chosen\0")?;
    let armio = child(&adt, 0, b"arm-io\0")?;
    let sgx = child(&adt, armio, b"sgx\0")?;
    let asc = child(&adt, armio, b"gfx-asc\0")?;
    let nub = child(&adt, asc, b"iop-gfx-nub\0")?;
    if required(&adt, sgx, b"compatible")? != b"gpu,t6034\0" ||
        u32_property(&adt, nub, b"pre-loaded")? != 1
    {
        return Err("native firmware source identity or preload");
    }
    let scalar = |key: &[u8]| -> R<u64> {
        let value = required(&adt, chosen, key)?;
        if value.len() != 8 {
            return Err("native firmware DRAM scalar");
        }
        le64(value, 0).ok_or("native firmware DRAM value")
    };
    let dram_start = scalar(b"dram-base")?;
    let dram_size = scalar(b"dram-size")?;
    let dram_end = dram_start.checked_add(dram_size)
        .filter(|_| dram_size != 0).ok_or("native firmware DRAM bounds")?;
    let names = required(&adt, asc, b"segment-names")?;
    if names != b"__TEXT;__DATA" && names != b"__TEXT;__DATA\0" {
        return Err("native firmware segment names");
    }
    let ranges = required(&adt, asc, b"segment-ranges")?;
    if ranges.len() != 64 || required(&adt, nub, b"segment-ranges")? != ranges {
        return Err("native firmware parent/nub segments");
    }
    let mut text_size = 0;
    for (index, expected) in [text, data].iter().enumerate() {
        let row = &ranges[index * 32..index * 32 + 32];
        let start = le64(row, 0).ok_or("native firmware physical base")?;
        let size = u64::from(le32(row, 24).ok_or("native firmware segment size")?);
        let end = start.checked_add(size).ok_or("native firmware segment end")?;
        if start == 0 || size == 0 || (start | size) & (hw::PAGE - 1) != 0 ||
            start < dram_start || end > dram_end || end - 1 > (hw::PA_MASK | (hw::PAGE - 1)) ||
            le64(row, 16) != Some(start) || le32(row, 28) != Some(if index == 0 { 1 } else { 0 }) ||
            expected.start != start || expected.end != end
        {
            return Err("native firmware physical description mismatch");
        }
        if index == 0 {
            text_size = size;
        }
        let va = hw::UPPER.checked_add(if index == 0 { 0 } else { text_size })
            .ok_or("native firmware virtual end")?;
        if le64(row, 8) != Some(va) {
            return Err("native firmware virtual description mismatch");
        }
    }
    if text.start < data.end && data.start < text.end {
        return Err("native firmware physical segments overlap");
    }
    Ok(())
}

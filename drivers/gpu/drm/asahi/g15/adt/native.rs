// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! Immutable views into one owned ADT copy. No mapping or device operation.
//! Shape checks do not attest firmware compatibility or hardware state.

use super::{le32, le64, Adt};
use crate::g15::board::record;

type R<T> = Result<T, &'static str>;

pub(crate) struct Properties<'a> {
    pub(crate) gpu_num_perf_states: u32,
    pub(crate) table_count: u32,
    pub(crate) main: &'a [u8],
    pub(crate) sram: Option<&'a [u8]>,
    pub(crate) cs: &'a [u8],
    pub(crate) afr: &'a [u8],
    pub(crate) sensor_rows: [[u8; 12]; record::SENSOR_ROW_COUNT],
}

// Exact complete names and duplicate refusal are separate from the legacy
// parser's prefix matching. Call only after complete structural validation.
pub(super) fn property<'a>(adt: &Adt<'a>, node: usize, key: &[u8]) -> R<Option<&'a [u8]>> {
    let count = le32(adt.data, node).ok_or("native ADT property count")?;
    let mut offset = node + 8;
    let mut found = None;
    for _ in 0..count {
        let field = &adt.data[offset..offset + 32];
        let end = field.iter().position(|&c| c == 0).unwrap_or(32);
        let length = (le32(adt.data, offset + 32).ok_or("native ADT property size")? & 0x7fff_ffff) as usize;
        if &field[..end] == key {
            if found.is_some() {
                return Err("native ADT duplicate property");
            }
            found = Some(&adt.data[offset + 36..offset + 36 + length]);
        }
        offset += 36 + length.next_multiple_of(4);
    }
    Ok(found)
}

pub(super) fn required<'a>(adt: &Adt<'a>, node: usize, key: &[u8]) -> R<&'a [u8]> {
    property(adt, node, key)?.ok_or("native ADT missing property")
}

pub(super) fn child(adt: &Adt<'_>, node: usize, name: &[u8]) -> R<usize> {
    let props = le32(adt.data, node).ok_or("native ADT property count")?;
    let children = le32(adt.data, node + 4).ok_or("native ADT child count")?;
    let mut offset = node + 8;
    for _ in 0..props {
        let length = (le32(adt.data, offset + 32).ok_or("native ADT property size")? & 0x7fff_ffff) as usize;
        offset += 36 + length.next_multiple_of(4);
    }
    let mut found = None;
    for _ in 0..children {
        if property(adt, offset, b"name")? == Some(name) {
            if found.is_some() {
                return Err("native ADT duplicate child");
            }
            found = Some(offset);
        }
        offset = adt.node_end(offset, 1).ok_or("native ADT child structure")?;
    }
    found.ok_or("native ADT missing exact child")
}

pub(super) fn u32_property(adt: &Adt<'_>, node: usize, key: &[u8]) -> R<u32> {
    let value = required(adt, node, key)?;
    if value.len() != 4 {
        return Err("native ADT scalar shape");
    }
    le32(value, 0).ok_or("native ADT scalar")
}

fn aux(bytes: &[u8], capacity: u64) -> R<()> {
    let channels = le64(bytes, 0).ok_or("native ADT auxiliary header")?;
    let count = le64(bytes, 8).ok_or("native ADT auxiliary header")?;
    if channels != 1 || !(2..=capacity).contains(&count) || bytes.len() < 24 + count as usize * 16 {
        return Err("native ADT auxiliary geometry");
    }
    Ok(())
}

pub(crate) fn parse(data: &[u8], chip: u32) -> R<Properties<'_>> {
    if chip != 0x6034 || data.len() > 0x20_0000 {
        return Err("native ADT chip or copy size");
    }
    let adt = Adt { data };
    adt.node_end(0, 0).ok_or("native ADT structure")?;
    let armio = child(&adt, 0, b"arm-io\0")?;
    let sgx = child(&adt, armio, b"sgx\0")?;
    if required(&adt, sgx, b"compatible")? != b"gpu,t6034\0" {
        return Err("native ADT GPU identity");
    }
    let gpu_num_perf_states = u32_property(&adt, sgx, b"gpu-num-perf-states")?;
    let table_count = u32_property(&adt, sgx, b"perf-state-table-count")?;
    if !(1..record::PERF_STATES as u32).contains(&gpu_num_perf_states) || table_count != 4 {
        return Err("native ADT main geometry");
    }
    if let Some(count) = property(&adt, sgx, b"perf-state-count")? {
        if count.len() != 4 || le32(count, 0) != Some(gpu_num_perf_states + 1) {
            return Err("native ADT contradictory main count");
        }
    }
    let main = required(&adt, sgx, b"perf-states")?;
    let sram = property(&adt, sgx, b"perf-states-sram")?;
    let need = (gpu_num_perf_states as usize + 1) * table_count as usize * 8;
    // Preserve original spans, including any source-accepted extension bytes.
    // Missing SRAM is distinct from a present empty/malformed property.
    if main.len() < need || sram.is_some_and(|value| value.len() < need) {
        return Err("native ADT main property span");
    }
    let cs = required(&adt, sgx, b"cs-perf-states")?;
    let afr = required(&adt, sgx, b"afr-perf-states")?;
    aux(cs, 16)?;
    aux(afr, 8)?;
    let ptd = child(&adt, armio, b"aon-ptd\0")?;
    let sensor_rows = record::sensor_rows(required(&adt, ptd, b"ptd_data_table")?)?;
    Ok(Properties { gpu_num_perf_states, table_count, main, sram, cs, afr, sensor_rows })
}

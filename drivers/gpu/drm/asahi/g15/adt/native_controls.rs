// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! GPU controller words from one immutable original ADT copy.
//! These are metadata, not qualified physical power/thermal limits. The two
//! legacy SRAM floor cells are unused by native record construction and stay
//! zero; native SRAM always comes from the complete imported table bundle.

use super::{le32, Adt};
use super::native::{child, property, required};
use crate::g15::board::{record, words};

type R<T> = Result<T, &'static str>;

pub(crate) struct Settings<'a> {
    source: &'a [u8],
    record: [u32; record::PROPS.len()],
    power: words::PowerWords,
}

fn scalar(bytes: &[u8]) -> R<u32> {
    if bytes.len() != 4 {
        return Err("native controller scalar shape");
    }
    le32(bytes, 0).ok_or("native controller scalar")
}

fn key<'a>(suffix: &[u8], storage: &'a mut [u8; 64]) -> R<&'a [u8]> {
    // The existing exporter and native source use gpu-se-tgt, while the
    // firmware record schema calls this field apple,se-target.
    let suffix = if suffix == b"se-target" { b"se-tgt" } else { suffix };
    let end = 4 + suffix.len();
    if end > storage.len() {
        return Err("native controller property name");
    }
    storage[..4].copy_from_slice(b"gpu-");
    storage[4..end].copy_from_slice(suffix);
    Ok(&storage[..end])
}

impl<'a> Settings<'a> {
    pub(super) fn parse(data: &'a [u8], chip: u32) -> R<Self> {
        if chip != 0x6034 || data.len() > 0x20_0000 {
            return Err("native controller source chip or size");
        }
        let adt = Adt { data };
        adt.node_end(0, 0).ok_or("native controller source structure")?;
        let armio = child(&adt, 0, b"arm-io\0")?;
        let sgx = child(&adt, armio, b"sgx\0")?;
        if required(&adt, sgx, b"compatible")? != b"gpu,t6034\0" {
            return Err("native controller GPU identity");
        }
        let mut values = [0; record::PROPS.len()];
        for (value, (name, default)) in values.iter_mut().zip(record::PROPS.iter()) {
            let suffix = name.to_bytes().strip_prefix(b"apple,")
                .ok_or("native controller record schema")?;
            if suffix == b"min-sram-microvolt" || suffix == b"csafr-min-sram-microvolt" {
                continue;
            }
            let mut storage = [0; 64];
            *value = match property(&adt, sgx, key(suffix, &mut storage)?)? {
                Some(bytes) => scalar(bytes)?,
                None => default.ok_or("native controller missing required property")?,
            };
        }
        let mut zones = [0; 3 * words::MAX_ZONES];
        let mut count = 0;
        let mut gap = false;
        // Reject a partial/holey set instead of silently dropping configured
        // rows after the first missing target. The native format has five rows.
        for index in 0..=words::MAX_ZONES {
            let mut row = [None; 3];
            for (cell, prefix) in row.iter_mut().zip([
                b"gpu-power-zone-target-".as_slice(),
                b"gpu-power-zone-target-offset-".as_slice(),
                b"gpu-power-zone-filter-tc-".as_slice(),
            ]) {
                let mut name = [0; 64];
                name[..prefix.len()].copy_from_slice(prefix);
                name[prefix.len()] = b'0' + index as u8;
                *cell = property(&adt, sgx, &name[..prefix.len() + 1])?;
            }
            if row.iter().all(Option::is_none) {
                gap = true;
                continue;
            }
            if gap || index == words::MAX_ZONES || row.iter().any(Option::is_none) {
                return Err("native controller incomplete or excessive zones");
            }
            for (value, bytes) in zones[3 * index..3 * index + 3].iter_mut().zip(row) {
                *value = scalar(bytes.ok_or("native controller zone cell")?)?;
            }
            if zones[3 * index] < zones[3 * index + 1] || zones[3 * index + 2] == 0 {
                return Err("native controller zone domain");
            }
            count += 1;
        }
        let mut power = [0; words::PROPS.len()];
        for (value, name) in power.iter_mut().zip(words::PROPS.iter()) {
            *value = if let Some(index) = record::PROPS.iter()
                .position(|(record_name, _)| record_name.to_bytes() == name.to_bytes())
            {
                values[index]
            } else if name.to_bytes() == b"apple,idleoff-standby-timer" {
                scalar(required(&adt, sgx, b"gpu-idleoff-standby-timer")?)?
            } else {
                return Err("native controller power schema");
            };
        }
        let power = words::PowerWords::new(power, &zones[..3 * count])?;
        Ok(Self { source: data, record: values, power })
    }

    pub(super) fn belongs_to(&self, data: &[u8]) -> bool {
        core::ptr::eq(self.source, data)
    }

    pub(crate) fn record(&self) -> &[u32; record::PROPS.len()] { &self.record }
    pub(crate) fn power(&self) -> &words::PowerWords { &self.power }
    pub(crate) fn zones(&self) -> &[u32] {
        self.power.zones[..self.power.zone_count as usize].as_flattened()
    }
}

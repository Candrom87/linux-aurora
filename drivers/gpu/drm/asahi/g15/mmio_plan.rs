// SPDX-License-Identifier: GPL-2.0-only OR MIT
//! Validate discontiguous physical device ranges before mapping them into one
//! contiguous firmware window. No existing board profile selects this path.

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct PhysRange {
    pub start: u64,
    pub len: u64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub(crate) struct Mapping {
    pub va: u64,
    pub pa: u64,
    pub len: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Error {
    BadPageSize,
    Empty,
    OutputTooSmall,
    Unaligned,
    AddressOverflow,
    PhysicalOverlap,
}

/// Create an all-or-nothing mapping plan. The caller must independently prove
/// that the ranges and access flags are valid for this firmware and hardware.
pub(crate) fn plan(va: u64, page: u64, ranges: &[PhysRange], out: &mut [Mapping]) -> Result<usize, Error> {
    if !page.is_power_of_two() { return Err(Error::BadPageSize); }
    if ranges.is_empty() { return Err(Error::Empty); }
    if ranges.len() > out.len() { return Err(Error::OutputTooSmall); }
    if va & (page - 1) != 0 { return Err(Error::Unaligned); }
    let mut end_va = va;
    for (i, r) in ranges.iter().enumerate() {
        if r.len == 0 || r.start & (page - 1) != 0 || r.len & (page - 1) != 0 {
            return Err(Error::Unaligned);
        }
        let end = r.start.checked_add(r.len).ok_or(Error::AddressOverflow)?;
        end_va = end_va.checked_add(r.len).ok_or(Error::AddressOverflow)?;
        for previous in &ranges[..i] {
            let prev_end = previous.start.checked_add(previous.len).ok_or(Error::AddressOverflow)?;
            if r.start < prev_end && previous.start < end { return Err(Error::PhysicalOverlap); }
        }
    }
    // Validation above is complete before we touch the caller's output.
    let mut cursor = va;
    for (dst, src) in out.iter_mut().zip(ranges) {
        *dst = Mapping { va: cursor, pa: src.start, len: src.len };
        cursor += src.len; // checked during the first pass
    }
    Ok(ranges.len())
}

/// Maximum number of discontiguous ranges for one firmware I/O window.
pub(crate) const MAX_RANGES: usize = 16;

/// Match an IOMapping's firmware-visible geometry to its complete physical list.
/// The physical field records the first range, while the firmware VA covers the
/// packed list. This validates layout only, not hardware, permissions or ABI.
pub(crate) fn validate_mapping(
    page: u64, va: u64, pa: u64, map_bytes: u64, delta: u64, total: u32, element: u32,
    ranges: &[PhysRange], out: &mut [Mapping; MAX_RANGES],
) -> Result<usize, Error> {
    if ranges.len() < 2 || ranges.len() > MAX_RANGES || delta != 0 ||
        ranges[0].start != pa || element == 0 ||
        ranges.iter().any(|r| r.len != u64::from(element)) ||
        u64::from(total) != map_bytes ||
        (ranges.len() as u64).checked_mul(u64::from(element)) != Some(map_bytes)
    {
        return Err(Error::Unaligned);
    }
    plan(va, page, ranges, out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn translate(maps: &[Mapping], va: u64) -> Option<u64> {
        maps.iter().find(|m| va >= m.va && va < m.va + m.len).map(|m| m.pa + va - m.va)
    }

    #[test]
    fn six_discontiguous_ranges_are_packed_in_firmware_va_order() {
        // Synthetic ranges exercise packing only; no board profile selects this path.
        let ranges: Vec<_> = (0..6).map(|n| PhysRange { start: 0x20_0000 + n*0x20_0000,
                                                       len: 0x8000 }).collect();
        let mut out = [Mapping::default(); 6];
        assert_eq!(plan(0x4_0000_0000, 0x4000, &ranges, &mut out), Ok(6));
        for i in 0..6 {
            let v = 0x4_0000_0000 + i as u64*0x8000;
            assert_eq!(translate(&out, v), Some(ranges[i].start));
            assert_eq!(translate(&out, v + 0x7fff), Some(ranges[i].start + 0x7fff));
        }
        assert_eq!(translate(&out, 0x4_0000_0000 + 6*0x8000), None);
        // The second physical block is not the next byte of the first.
        assert_ne!(out[1].pa, out[0].pa + out[0].len);
    }

    #[test]
    fn failures_do_not_publish_a_partial_plan() {
        let marker = Mapping { va: 1, pa: 2, len: 3 };
        let good = PhysRange { start: 0x4000, len: 0x4000 };
        for ranges in [vec![], vec![good, good], vec![good, PhysRange {start: 0x8001, len: 0x4000}],
                       vec![good, PhysRange {start: u64::MAX - 0x3fff, len: 0x4000}]] {
            let mut out = [marker; 2];
            assert!(plan(0x4000, 0x4000, &ranges, &mut out).is_err());
            assert_eq!(out, [marker; 2]);
        }
        let mut out = [marker; 2];
        assert_eq!(plan(u64::MAX - 0x3fff, 0x4000, &[good], &mut out), Err(Error::AddressOverflow));
        assert_eq!(plan(0x4000, 0x3000, &[good], &mut out), Err(Error::BadPageSize));
        assert_eq!(plan(0x4000, 0x4000, &[good, good, good], &mut out), Err(Error::OutputTooSmall));
        assert_eq!(out, [marker; 2]);
    }

    #[test]
    fn firmware_record_must_describe_the_whole_scatter_window() {
        let ranges = [
            PhysRange { start: 0x4000, len: 0x8000 },
            PhysRange { start: 0x200000, len: 0x8000 },
        ];
        let marker = Mapping { va: 1, pa: 2, len: 3 };
        let mut out = [marker; MAX_RANGES];
        assert_eq!(validate_mapping(0x4000, 0x800000, 0x4000, 0x10000, 0,
                                    0x10000, 0x8000, &ranges, &mut out), Ok(2));
        assert_eq!(out[1], Mapping { va: 0x808000, pa: 0x200000, len: 0x8000 });
        let mut out = [marker; MAX_RANGES];
        for (pa, bytes, delta, total, element) in [
            (0x8000, 0x10000, 0, 0x10000, 0x8000),
            (0x4000, 0x14000, 0, 0x10000, 0x8000),
            (0x4000, 0x10000, 1, 0x10000, 0x8000),
            (0x4000, 0x10000, 0, 0x10000, 0x4000),
        ] {
            assert!(validate_mapping(0x4000, 0x800000, pa, bytes, delta,
                                     total, element, &ranges, &mut out).is_err());
            assert_eq!(out, [marker; MAX_RANGES]);
        }
    }
}

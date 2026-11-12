// SPDX-License-Identifier: GPL-2.0-only OR MIT

//! KUnit-only bridge to the production OF reader and board-image preflight.
//! Every performance/sensor/region value below is invented. No hardware access.

use super::*;
use kernel::bindings;

/// Static names and the same valid synthetic scalar words used by the core
/// record tests. The C fixture installs them as actual OF properties.
///
/// # Safety
/// `value` points to an independently writable u32 for this call. The returned
/// name is static kernel data and must not be freed by C.
#[no_mangle]
unsafe extern "C" fn asahi_g15_native_board_test_prop(
    index: u32,
    value: *mut u32,
) -> *const core::ffi::c_char {
    let Some((name, _)) = record::PROPS.get(index as usize) else {
        return core::ptr::null();
    };
    if value.is_null() {
        return core::ptr::null();
    }
    // SAFETY: The C fixture owns the u32 output for this synchronous call.
    unsafe {
        value.write(record::synthetic_props()[index as usize]);
    }
    name.as_char_ptr()
}

fn fixture_adt() -> GpuAdt {
    let mut adt = GpuAdt {
        ttbs: 0x4000..0x8000,
        tables: 0x8000..0x48000,
        handoff: 0x48000..0x4c000,
        shared_l2: 0x4c000..0x50000,
        text: 0x100000..0x104000,
        data: 0x104000..0x108000,
        perf_mhz: [0; record::PERF_STATES],
        perf_mv: [0; record::PERF_STATES],
        sram_mv: [800; record::PERF_STATES],
        cluster_mv: [[0; 4]; record::PERF_STATES],
        cluster_cells: 4,
        sensor_rows: [[0; 12]; record::SENSOR_ROW_COUNT],
    };
    for n in 0..record::PERF_STATES {
        adt.perf_mhz[n] = 100 * n as u32;
        adt.perf_mv[n] = if n == 0 { 125 } else { 500 + 20 * n as u32 };
        adt.cluster_mv[n] = [adt.perf_mv[n]; 4];
    }
    for (row, key) in adt
        .sensor_rows
        .iter_mut()
        .zip([b"RT0g", b"RT1g", b"RT2g", b"RT3g", b"RT0r", b"RT0f"])
    {
        row[..4].copy_from_slice(key);
    }
    adt
}

fn word(bytes: &[u8], at: usize) -> Result<u32> {
    Ok(u32::from_le_bytes(
        bytes
            .get(at..at + 4)
            .ok_or(EINVAL)?
            .try_into()
            .map_err(|_| EINVAL)?,
    ))
}

fn run(dev: &device::Device, chip: u32, full: bool, output: &mut [u32]) -> Result<usize> {
    // In full mode, run the configured production route FIRST. The missing
    // property case must fail here, without silently selecting J516C data.
    let images = if full {
        if chip != 0x6034 {
            return Err(EINVAL);
        }
        Some(super::super::initdata::prepare_board(
            dev,
            &hw::t6034::CONFIG,
            &fixture_adt(),
        )?)
    } else {
        None
    };
    let reference = native_reference(dev, chip)?;
    let record::GroupingEvidence::Native {
        count,
        primary,
        secondary,
    } = reference.grouping
    else {
        return Err(EINVAL);
    };
    let len = record::NATIVE_HEADER_CELLS + 3 * count;
    let mut cells = [0u32; record::NATIVE_MAX_CELLS + 6];
    cells[..4].copy_from_slice(&[
        record::NATIVE_CONTRACT,
        chip,
        reference.max_power_mw,
        count as u32,
    ]);
    for (offset, mask) in [
        reference.fast_die_mask,
        reference.sochot_mask,
        reference.alarm_mask,
    ]
    .iter()
    .enumerate()
    {
        for limb in 0..4 {
            cells[4 + 4 * offset + limb] = (*mask >> (96 - 32 * limb)) as u32;
        }
    }
    for n in 0..count {
        cells[16 + n] = primary[n] as u32;
        cells[16 + count + n] = secondary[n] as u32;
        cells[16 + 2 * count + n] = reference.relative_power.as_slice()[n];
    }
    let len = if let Some((hardware, globals, power, khz)) = images {
        if word(&hardware, hwdata::DVFS_COUNT)? != count as u32 - 1
            || word(&hardware, record::MAX_POWER)? != reference.max_power_mw
            || word(&globals, 0x78)? != reference.max_power_mw
            || word(&power, 0xd0)? != reference.max_power_mw
        {
            return Err(EINVAL);
        }
        for at in [0x7c, 0x80, 0x8c] {
            if word(&globals, at)? != 100 * (count as u32 - 1) {
                return Err(EINVAL);
            }
        }
        for (offset, mask) in [
            (0x8ac, reference.fast_die_mask),
            (0x1490, reference.sochot_mask),
            (0x24a8, reference.alarm_mask),
        ] {
            let at = record::HWDATA_A + offset;
            if hardware.get(at..at + 16) != Some(mask.to_le_bytes().as_slice()) {
                return Err(EINVAL);
            }
        }
        for (n, &ratio) in reference.relative_power.as_slice().iter().enumerate() {
            if word(&hardware, 0x105c + 4 * n)? != ratio {
                return Err(EINVAL);
            }
        }
        let table = super::super::power::pstate::Table::from_record(&hardware).ok_or(EINVAL)?;
        if table.khz(count as u32 - 1) != Some(khz) || table.khz(count as u32).is_some() {
            return Err(EINVAL);
        }
        cells[len..len + 6].copy_from_slice(&[
            hardware.len() as u32,
            globals.len() as u32,
            power.len() as u32,
            word(&hardware, 0xa28)?,
            word(&hardware, record::IOMAPS + 32 * 11 + 0x10)?,
            khz,
        ]);
        len + 6
    } else {
        len
    };
    // Ref owns every field after native_reference's temporary Property/KVec
    // has gone out of scope. No bytes are exposed on a returned refusal.
    output[..len].copy_from_slice(&cells[..len]);
    Ok(len)
}

/// # Safety
/// C KUnit owns an initialized, referenced platform device for this entire
/// call and a disjoint writable array of exactly 70 u32 words. It registers
/// no device or driver and balances the OF references after returning.
#[no_mangle]
unsafe extern "C" fn asahi_g15_native_board_test(
    raw: *mut bindings::device,
    chip: u32,
    full: bool,
    output: *mut u32,
) -> i32 {
    if raw.is_null() || output.is_null() {
        return EINVAL.to_errno();
    }
    // SAFETY: The C test's retained device and independent array satisfy the
    // contract above. No Bound context, registration or hardware is assumed.
    let dev = unsafe { device::Device::from_raw(raw) };
    // SAFETY: The caller owns the specified 70-word test array.
    let output = unsafe { core::slice::from_raw_parts_mut(output, 70) };
    match run(dev, chip, full, output) {
        Ok(len) => len as i32,
        Err(error) => error.to_errno(),
    }
}

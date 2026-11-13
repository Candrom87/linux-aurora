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
        native: None,
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
    if full {
        let _ = super::super::initdata::prepare_board(
            dev, &hw::t6034::CONFIG, &fixture_adt(), None, None,
        )?;
        // A packet-only preflight unexpectedly succeeded. Keep this distinct
        // from its required EINVAL refusal so the C assertion detects it.
        return Err(EOPNOTSUPP);
    }
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


fn native_images(dev: &device::Device, bundle: &native::Bundle, output: &mut [u32]) -> Result {
    let bound = native::Bound::new(bundle, 0x6034).map_err(|_| EINVAL)?;
    let reference = bound.reference();
    let tables = bound.tables();
    let record::GroupingEvidence::Native { count, primary, secondary } = reference.grouping else {
        return Err(EINVAL);
    };
    let mut adt = fixture_adt();
    adt.native = Some(super::super::adt::NativeAdt::read(dev, 0x6034)?);
    adt.sensor_rows = adt.native.as_ref().ok_or(EINVAL)?.properties(0x6034)?.sensor_rows;
    // Poison the unrelated legacy cache. No OPP phandles exist on the test
    // device, so reconstructing performance input cannot silently succeed.
    adt.perf_mhz.fill(u32::MAX);
    adt.perf_mv.fill(u32::MAX);
    adt.cluster_mv.fill([u32::MAX; 4]);
    adt.cluster_cells = 0;
    adt.sram_mv.fill(1);
    let (hardware, globals, power, khz) = super::super::initdata::fixture_bundle_board(
        dev, &hw::t6034::CONFIG, &adt, bundle,
    )?;
    if count != 9 || word(&hardware, hwdata::DVFS_COUNT)? != 8
        || word(&hardware, record::MAX_POWER)? != reference.max_power_mw
        || word(&globals, 0x78)? != reference.max_power_mw
        || word(&power, 0xd0)? != reference.max_power_mw
        || word(&globals, 0x7c)? != 600 || word(&globals, 0x80)? != 600
        || word(&globals, 0x8c)? != 700 || khz != 713000
    {
        return Err(EINVAL);
    }
    for (offset, mask) in [(0x8ac, reference.fast_die_mask),
        (0x1490, reference.sochot_mask), (0x24a8, reference.alarm_mask)]
    {
        let at = record::HWDATA_A + offset;
        if hardware.get(at..at + 16) != Some(mask.to_le_bytes().as_slice()) {
            return Err(EINVAL);
        }
    }
    for state in 0..count {
        let a = primary[state] as usize;
        let b = secondary[state] as usize;
        if word(&hardware, 0xb5c + state * 4)? != tables.main.hz[a] / 1_000_000
            || word(&hardware, 0xf9c + state * 4)? != tables.main.hz[b] / 1_000_000
            || word(&hardware, 0x1350 + state * 4)? != tables.main.hz[b] / 1_000_000
            || word(&hardware, 0x10dc + state * 4)? != primary[state] as u32
            || word(&hardware, 0x111c + state * 4)? != secondary[state] as u32
            || word(&hardware, 0x105c + state * 4)? != reference.relative_power.as_slice()[state]
        {
            return Err(EINVAL);
        }
        for lane in 0..8 {
            if word(&hardware, 0xb9c + state * 32 + lane * 4)? != tables.main.mv[a][lane]
                || word(&hardware, 0xd9c + state * 32 + lane * 4)? != tables.main.sram_mv[a][lane]
            {
                return Err(EINVAL);
            }
        }
        for lane in 0..4 {
            if word(&hardware, 0x1390 + state * 32 + lane * 4)? != tables.main.mv[b][lane]
                || word(&hardware, 0x1590 + state * 32 + lane * 4)? != tables.main.sram_mv[b][lane]
            {
                return Err(EINVAL);
            }
        }
    }
    for (base, capacity, raw, cap) in [(0x115c, 16, &tables.cs, tables.caps[1]),
        (0x12a4, 8, &tables.afr, tables.caps[2])]
    {
        if word(&hardware, base)? != raw.count - 1 || word(&hardware, base + 4)? != cap {
            return Err(EINVAL);
        }
        for row in 0..raw.count as usize {
            if word(&hardware, base + 8 + row * 4)? != raw.hz[row] / 1_000_000
                || word(&hardware, base + 8 + capacity * 4 + row * 8)? != raw.mv[row][0]
                || word(&hardware, base + 8 + capacity * 12 + row * 8)? != raw.sram_mv[row][0]
            {
                return Err(EINVAL);
            }
        }
    }
    for (row, expected) in adt.sensor_rows.iter().enumerate() {
        let at = record::HWDATA_A + 0x6c58 + 12 * row;
        if hardware.get(at..at + 12) != Some(expected.as_slice()) {
            return Err(EINVAL);
        }
    }
    let status = super::super::power::pstate::Table::from_record(&hardware).ok_or(EINVAL)?;
    if status.khz(8) != Some(660000) || status.khz(9).is_some() { return Err(EINVAL); }
    output.copy_from_slice(&[hardware.len() as u32, globals.len() as u32,
        power.len() as u32, word(&hardware, 0xa28)?,
        word(&hardware, record::IOMAPS + 32 * 11 + 0x10)?, khz]);
    Ok(())
}

/// C owns a live unregistered device, an aligned initialized immutable C bundle
/// and a disjoint six-u32 output for the entire synchronous call. No hardware.
#[no_mangle]
unsafe extern "C" fn asahi_g15_native_bundle_images_test(
    raw: *mut bindings::device, bundle: *const core::ffi::c_void,
    length: usize, output: *mut u32,
) -> i32 {
    if raw.is_null() || bundle.is_null() || output.is_null()
        || length != core::mem::size_of::<native::Bundle>()
    {
        return EINVAL.to_errno();
    }
    // SAFETY: The retained C device, exactly matching integer C ABI allocation
    // and independent output satisfy the stated synchronous test contract.
    let (dev, bundle, output) = unsafe { (device::Device::from_raw(raw),
        &*bundle.cast::<native::Bundle>(), core::slice::from_raw_parts_mut(output, 6)) };
    match native_images(dev, bundle, output) {
        Ok(()) => 6,
        Err(error) => error.to_errno(),
    }
}


fn owned_images(dev: &device::Device, values: &[u32], output: &mut [u32]) -> Result {
    // The same deliberately malformed OF zones still refuse on the legacy
    // reader. Native success below must therefore use the original owner.
    if super::power_words(dev).err() != Some(EINVAL) { return Err(EINVAL); }
    let mut adt = fixture_adt();
    adt.native = Some(super::super::adt::NativeAdt::read(dev, 0x6034)?);
    // Invented boot descriptors in the marked QEMU export. No firmware bytes
    // are read: every real profile remains unqualified for native production.
    adt.text = 0x10a180000..0x10a184000;
    adt.data = 0x10a184000..0x10a188000;
    if super::super::identity::native_firmware(dev, &adt, hw::t6034::CONFIG.firmware,
        0x6034).err() != Some(EINVAL)
    {
        return Err(EINVAL);
    }
    let firmware = super::super::identity::fixture_native_firmware(dev, &adt)?;
    let words = native::Words {
        identity: values[..6].try_into().map_err(|_| EINVAL)?,
        coefficients: values[6..10].try_into().map_err(|_| EINVAL)?,
        coefficients_present: values[10],
        map_banks: [values[11..19].try_into().map_err(|_| EINVAL)?,
            values[19..27].try_into().map_err(|_| EINVAL)?],
        cs_activity_mask: values[27],
    };
    // Equal bytes copied again must not impersonate this source allocation.
    let other = super::super::adt::NativeAdt::read(dev, 0x6034)?;
    if firmware.matches_source(&other) { return Err(EINVAL); }
    let original = adt.native.replace(other).ok_or(EINVAL)?;
    if super::super::initdata::prepare_board(dev, &hw::t6034::CONFIG, &adt,
        Some(&words), Some(&firmware)).err() != Some(EINVAL)
    {
        return Err(EINVAL);
    }
    let other = adt.native.replace(original).ok_or(EINVAL)?;
    if !firmware.matches(dev, adt.native.as_ref().ok_or(EINVAL)?) {
        return Err(EINVAL);
    }
    let snapshot = adt.native.as_ref().ok_or(EINVAL)?;
    let bundle = super::native_bundle(snapshot, &firmware, &words, 0x6034)?;
    let bound = native::Bound::new(&bundle, 0x6034).map_err(|_| EINVAL)?;
    let spliced = native::Source::Native(bound, other.controllers(0x6034)?);
    if super::hardware_record(dev, &hw::t6034::CONFIG, &adt, &spliced).err() != Some(EINVAL) {
        return Err(EINVAL);
    }
    adt.perf_mhz.fill(u32::MAX);
    adt.perf_mv.fill(u32::MAX);
    adt.cluster_mv.fill([u32::MAX; 4]);
    adt.cluster_cells = 0;
    adt.sensor_rows.fill([0xff; 12]);
    let (hardware, globals, power, khz) = super::super::initdata::prepare_board(
        dev, &hw::t6034::CONFIG, &adt, Some(&words), Some(&firmware),
    )?;
    // The separate QEMU original table uses 200 + 40*n MHz for rows 1..13;
    // both selected maps retain row 13, so the maximum is 720 MHz.
    if khz != 720000 {
        dev_err!(dev, "invented controller fixture frequency {} != 720000\n", khz);
        return Err(EINVAL);
    }
    let model = word(&hardware, record::MAX_POWER)?;
    if model == 0 { return Err(EINVAL); }
    // These values are invented in the marked QEMU fixture. Keep field-level
    // diagnostics here so a model/controller regression identifies its word.
    for (label, image, at, expected) in [
        ("selected top", hardware.as_slice(), hwdata::DVFS_COUNT, 8),
        ("first target", globals.as_slice(), 0x7c, 600),
        ("second target", globals.as_slice(), 0x80, 600),
        ("main cap", globals.as_slice(), 0x8c, 700),
        ("Globals model", globals.as_slice(), 0x78, model),
        ("Power model", power.as_slice(), 0xd0, model),
        ("sample period", hardware.as_slice(), 0xa70, 8),
        ("reset override", hardware.as_slice(), record::HWDATA_A + 0x798, 9),
        // G15C expands the earlier mask/coefficient fields: these are the
        // independent final offsets, not the G15S writer's source offsets.
        ("delta override", hardware.as_slice(), record::HWDATA_A + 0x908, 300),
        ("hardware zones", hardware.as_slice(), record::HWDATA_A + 0x108c, 2),
        ("standby", globals.as_slice(), 0x7a8, 99),
        ("power zones", power.as_slice(), 0xc, 2),
        ("zone target", power.as_slice(), 0x24, 5000),
        ("zone offset", power.as_slice(), 0x28, 4800),
        ("zone filter", power.as_slice(), 0x2c, 8),
        ("integral product", power.as_slice(), 0x14, 0x3dcc_cccd),
    ] {
        let actual = word(image, at)?;
        if actual != expected {
            dev_err!(dev, "invented controller fixture {} at {:#x}: {:#x} != {:#x}\n",
                label, at, actual, expected);
            return Err(EINVAL);
        }
    }
    let snapshot = adt.native.as_ref().ok_or(EINVAL)?;
    let properties = snapshot.properties(0x6034)?;
    let primary = [0usize, 1, 4, 6, 8, 10, 12, 13, 12];
    let secondary = [0usize, 1, 3, 5, 7, 9, 11, 12, 13];
    let hz_source = properties.sram.unwrap_or(properties.main);
    let raw_count = properties.gpu_num_perf_states as usize + 1;
    let le = |bytes: &[u8], at: usize| -> Result<u32> {
        Ok(u32::from_le_bytes(bytes.get(at..at + 4).ok_or(EINVAL)?
            .try_into().map_err(|_| EINVAL)?))
    };
    for state in 0..primary.len() {
        for (index, offset) in [(primary[state], 0xb5c), (secondary[state], 0xf9c),
            (secondary[state], 0x1350)]
        {
            let hz = le(hz_source, (3 * raw_count + index) * 8)?;
            if word(&hardware, offset + state * 4)? != hz / 1_000_000 { return Err(EINVAL); }
        }
        for lane in 0..8 {
            let source_lane = if lane < 4 { lane } else { 0 };
            let at = (source_lane * raw_count + primary[state]) * 8 + 4;
            if word(&hardware, 0xb9c + state * 32 + lane * 4)? != le(properties.main, at)?
                || word(&hardware, 0xd9c + state * 32 + lane * 4)? != le(properties.sram.ok_or(EINVAL)?, at)?
            { return Err(EINVAL); }
        }
    }
    for (row, value) in properties.sensor_rows.iter().enumerate() {
        let at = record::HWDATA_A + 0x6c58 + row * 12;
        if hardware.get(at..at + 12) != Some(value.as_slice()) { return Err(EINVAL); }
    }
    let membership = snapshot.mtr_membership(0x6034).map_err(|_| EINVAL)?;
    if membership.records() != 128 || membership.unique_ids() != 128 { return Err(EINVAL); }
    for (offset, expected) in [(0x8ac, 0x802a002b802a002bu128),
        (0x1490, 0x802a002b802a002bu128), (0x24a8, u128::MAX)]
    {
        let at = record::HWDATA_A + offset;
        if hardware.get(at..at + 16) != Some(expected.to_le_bytes().as_slice()) { return Err(EINVAL); }
    }
    let missing = native::Words { coefficients_present: 0, ..words };
    if super::super::initdata::prepare_board(dev, &hw::t6034::CONFIG, &adt,
        Some(&missing), Some(&firmware)).err() != Some(EINVAL)
    { return Err(EINVAL); }
    output.copy_from_slice(&[hardware.len() as u32, globals.len() as u32, power.len() as u32,
        word(&hardware, 0xa28)?, word(&hardware, record::IOMAPS + 32 * 11 + 0x10)?, khz]);
    Ok(())
}

/// # Safety
/// The marked C fixture retains its device, 28 immutable words and independent
/// six-word output for this synchronous CPU/metadata-only call.
#[no_mangle]
unsafe extern "C" fn asahi_g15_native_owned_images_test(
    raw: *mut bindings::device, words: *const u32, output: *mut u32,
) -> i32 {
    if raw.is_null() || words.is_null() || output.is_null() { return EINVAL.to_errno(); }
    // SAFETY: The C fixture's retained allocations satisfy the contract above.
    let (dev, words, output) = unsafe { (device::Device::from_raw(raw),
        core::slice::from_raw_parts(words, 28), core::slice::from_raw_parts_mut(output, 6)) };
    match owned_images(dev, words, output) {
        Ok(()) => 6,
        Err(error) => error.to_errno(),
    }
}

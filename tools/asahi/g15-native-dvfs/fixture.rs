// SPDX-License-Identifier: MIT
    #[repr(C)]
    #[derive(Copy, Clone)]
    struct ImportedBundle {
        length: u32,
        packet: [u8; 256],
        tables: NativeTables,
    }

    #[test]
    fn native_c_bundle_to_complete_record() {
        use std::io::Read;
        assert_eq!(core::mem::size_of::<ImportedBundle>(), 2024);
        let mut bytes = Vec::new();
        std::io::stdin().read_to_end(&mut bytes).unwrap();
        assert!(!bytes.is_empty() && bytes.len() % 2024 == 0);
        let mut records = Vec::new();
        let mut globals = Vec::new();
        for raw in bytes.chunks_exact(2024) {
            // Test-only all-u32 repr(C) transport, potentially unaligned input.
            let bundle = unsafe { core::ptr::read_unaligned(raw.as_ptr().cast::<ImportedBundle>()) };
            let cells: Vec<u32> = bundle.packet[..bundle.length as usize].chunks_exact(4)
                .map(|v| u32::from_be_bytes(v.try_into().unwrap())).collect();
            let reference = Reference::from_native_cells(0x6034, &cells).unwrap();
            let mut input = inputs(&[], &[], &[]);
            input.reference = &reference;
            input.chip_id = 0x6034;
            // These legacy floors intentionally conflict with native SRAM.
            input.props[MIN_SRAM_UV] = 1_400_000;
            input.props[CSAFR_MIN_SRAM_UV] = 1_400_000;
            let mut out = vec![0xa5; SIZE];
            build_native(&mut out, &input, &bundle.tables).unwrap();
            records.extend_from_slice(&out);
            let mut glob = vec![0xa5; super::super::words::GLOBALS_SIZE];
            native_globals(&mut glob, &out, &bundle.tables).unwrap();
            // Preserve a complete image so the independent oracle can also
            // reject accidental writes outside the three limits.
            globals.extend_from_slice(&glob);
            for case in 0..5 {
                let mut bad = out.clone();
                match case {
                    0 => { bad.pop(); },
                    1 => put32(&mut bad, 0xb58, u32::MAX).unwrap(),
                    2 => put32(&mut bad, 0xb58, 16).unwrap(),
                    3 => put32(&mut bad, 0xb5c, 4295).unwrap(),
                    _ => put32(&mut bad, a(0x854), u32::MAX).unwrap(),
                }
                let before = glob.clone();
                assert!(native_globals(&mut glob, &bad, &bundle.tables).is_err());
                assert_eq!(glob, before);
            }
            for case in 0..8 {
                let mut bad = bundle.tables;
                match case {
                    0 => bad.main.count = 1,
                    1 => bad.main.count = 15,
                    2 => bad.main.tables = 1,
                    3 => bad.cs.channels = 2,
                    4 => bad.afr.count = 9,
                    5 => bad.caps[1] = bad.cs.count,
                    6 => bad.cs.sram_mv[1][0] ^= 1,
                    _ => bad.caps[0] = cells[3],
                }
                assert!(build_native(&mut out, &input, &bad).is_err());
            }
        }
        std::fs::write(std::env::var_os("G15_NATIVE_RECORD_OUTPUT").unwrap(), records).unwrap();
        std::fs::write(std::env::var_os("G15_NATIVE_GLOBALS_OUTPUT").unwrap(), globals).unwrap();
    }

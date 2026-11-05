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
        for raw in bytes.chunks_exact(2024) {
            // Test-only all-u32 repr(C) transport, potentially unaligned input.
            let bundle = unsafe { core::ptr::read_unaligned(raw.as_ptr().cast::<ImportedBundle>()) };
            let cells: Vec<u32> = bundle.packet[..bundle.length as usize].chunks_exact(4)
                .map(|v| u32::from_be_bytes(v.try_into().unwrap())).collect();
            let reference = Reference::from_native_cells(0x6034, &cells).unwrap();
            let mut input = inputs(&[], &[], &[]);
            input.reference = &reference;
            // These legacy floors intentionally conflict with native SRAM.
            input.props[MIN_SRAM_UV] = 1_400_000;
            input.props[CSAFR_MIN_SRAM_UV] = 1_400_000;
            let mut out = vec![0xa5; SIZE];
            build_native(&mut out, &input, &bundle.tables).unwrap();
            records.extend_from_slice(&out);
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
    }

// SPDX-License-Identifier: MIT
#![allow(dead_code, unused_imports)]
use std::io::Read;

mod g15 {
    pub mod board {
        #[path = "WORDS"] pub mod words;
        #[path = "RECORD"] pub mod record;
    }
    pub mod adt {
        ADT_HELPERS
        #[path = "NATIVE"] pub mod native;
        #[path = "CONTROLS"] pub mod native_controls;
        pub fn parse(data: &[u8], chip: u32) -> Result<native_controls::Settings<'_>, &'static str> {
            native_controls::Settings::parse(data, chip)
        }
        pub fn belongs(value: &native_controls::Settings<'_>, data: &[u8]) -> bool {
            value.belongs_to(data)
        }
    }
}

fn main() {
    let mut stream = Vec::new();
    std::io::stdin().read_to_end(&mut stream).unwrap();
    let mut at = 0;
    let mut frame = 0;
    while at < stream.len() {
        let cell = |n| u32::from_le_bytes(stream[n..n + 4].try_into().unwrap());
        let chip = cell(at);
        let want = cell(at + 4) != 0;
        let length = cell(at + 8) as usize;
        at += 12;
        let data = &stream[at..at + length];
        at += length;
        let settings = g15::adt::parse(data, chip);
        assert_eq!(settings.is_ok(), want, "frame {}", frame);
        if let Ok(settings) = settings {
            let expected: Vec<u32> = stream[at..at + 70 * 4].chunks_exact(4)
                .map(|bytes| u32::from_le_bytes(bytes.try_into().unwrap())).collect();
            at += 70 * 4;
            assert_eq!(settings.record().as_slice(), &expected[..43]);
            assert_eq!(settings.power().values.as_slice(), &expected[43..54]);
            assert_eq!(settings.power().zone_count, expected[54]);
            assert_eq!(settings.zones(), &expected[55..55 + 3 * expected[54] as usize]);
            assert!(g15::adt::belongs(&settings, data));
            let other = data.to_vec();
            assert!(!g15::adt::belongs(&settings, &other));
        }
        frame += 1;
    }
    println!("{} controller metadata frames", frame);
}

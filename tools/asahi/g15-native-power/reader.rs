// SPDX-License-Identifier: MIT
#![allow(dead_code)]
mod board {
    #[path = "WORDS"] pub mod words;
    #[path = "RECORD"] pub mod record;
}
use board::record::{GroupingEvidence, Reference};
use std::io::Read;
fn main() {
    let mut input = Vec::new();
    std::io::stdin().read_to_end(&mut input).unwrap();
    let mut at = 0;
    while at < input.len() {
        let size = u32::from_le_bytes(input[at..at + 4].try_into().unwrap()) as usize;
        at += 4;
        assert!((88..=256).contains(&size) && size % 4 == 0);
        let cells: Vec<u32> = input[at..at + size].chunks_exact(4)
            .map(|b| u32::from_be_bytes(b.try_into().unwrap())).collect();
        let r = Reference::from_native_cells(0x6034, &cells).unwrap();
        assert_eq!(r.chip_id, cells[1]);
        assert_eq!(r.max_power_mw, cells[2]);
        let n = cells[3] as usize;
        match r.grouping {
            GroupingEvidence::Native { count, primary, secondary } => {
                assert_eq!(count, n);
                for i in 0..n {
                    assert_eq!(u32::from(primary[i]), cells[16 + i]);
                    assert_eq!(u32::from(secondary[i]), cells[16 + n + i]);
                }
            }
            _ => panic!("unexpected grouping"),
        }
        assert_eq!(r.relative_power.as_slice(), &cells[16 + 2 * n..]);
        let mask = |offset: usize| cells[offset..offset + 4].iter()
            .fold(0u128, |value, &word| value << 32 | u128::from(word));
        assert_eq!(r.fast_die_mask, mask(4));
        assert_eq!(r.sochot_mask, mask(8));
        assert_eq!(r.alarm_mask, mask(12));
        at += size;
    }
}

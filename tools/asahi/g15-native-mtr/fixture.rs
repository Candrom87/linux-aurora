// SPDX-License-Identifier: MIT
#![allow(dead_code)]
use std::io::Read;
mod adt {
    ADT_HELPERS
    mod native {
        use super::{Adt, le32};
        type R<T> = Result<T, &'static str>;
        NATIVE_HELPERS
    }
    #[path="MTR_SOURCE"] pub mod native_mtr;
    LIFETIME_PROBE
    pub fn check(index: u32, data: &[u8], chip: u32, expected: Option<([u32; 4], usize, u32)>) {
        match (native_mtr::Membership::new(data, chip), expected) {
            (Err(_), None) => {},
            (Ok(value), Some((limbs, records, unique))) => {
                assert_eq!(value.limbs_msb(), limbs, "case {index}: membership");
                assert_eq!(value.records(), records, "case {index}: record count");
                assert_eq!(value.unique_ids(), unique, "case {index}: unique IDs");
            },
            (Err(why), Some(_)) => panic!("case {index}: valid membership refused: {why}"),
            (Ok(_), None) => panic!("case {index}: invalid membership accepted"),
        }
    }
}
fn take<'a>(bytes: &mut &'a [u8], length: usize) -> &'a [u8] {
    let (value, rest) = bytes.split_at(length);
    *bytes = rest;
    value
}
fn u32le(bytes: &mut &[u8]) -> u32 {
    u32::from_le_bytes(take(bytes, 4).try_into().unwrap())
}
fn main() {
    // Expected mutation failures must not generate desktop crash events.
    std::panic::set_hook(Box::new(|info| { eprintln!("{info}"); std::process::exit(101); }));
    let mut input = Vec::new();
    std::io::stdin().read_to_end(&mut input).unwrap();
    let mut bytes = input.as_slice();
    let count = u32le(&mut bytes);
    for index in 0..count {
        let chip = u32le(&mut bytes);
        let valid = u32le(&mut bytes) != 0;
        let length = u32le(&mut bytes) as usize;
        let mut limbs = [0; 4];
        for value in &mut limbs { *value = u32le(&mut bytes); }
        let records = u32le(&mut bytes) as usize;
        let unique = u32le(&mut bytes);
        let data = take(&mut bytes, length);
        let before = data.to_vec();
        adt::check(index, data, chip, valid.then_some((limbs, records, unique)));
        assert_eq!(data, before.as_slice());
    }
    assert!(bytes.is_empty());
    println!("{count} actual MTR membership cases; invented data; no device I/O");
}

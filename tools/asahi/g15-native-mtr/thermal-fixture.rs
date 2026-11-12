// SPDX-License-Identifier: MIT
#![allow(dead_code)]
use std::io::Read;
mod g15 {
    // The real NativeFirmware's measured-image construction is covered by
    // its own identity suite. Only its immutable contract accessor is modeled.
    pub mod identity {
        pub struct Build { pub native_board_contract: Option<u32> }
        pub struct NativeFirmware { pub build: Build }
        impl NativeFirmware { pub fn build(&self) -> &Build { &self.build } }
    }
    pub mod adt {
        ADT_HELPERS
        mod native {
            use super::{Adt, le32};
            type R<T> = Result<T, &'static str>;
            NATIVE_HELPERS
        }
        #[path="MTR_SOURCE"] pub mod native_mtr;
        pub fn membership(data: &[u8]) -> native_mtr::Membership<'_> {
            native_mtr::Membership::new(data, 0x6034).unwrap()
        }
    }
    pub mod board {
        pub mod record { pub const NATIVE_CONTRACT: u32 = 0x23c22001; }
        pub mod native { #[path="THERMAL_SOURCE"] pub mod thermal; }
    }
}
LIFETIME_PROBE
fn take<'a>(bytes: &mut &'a [u8], length: usize) -> &'a [u8] {
    let (value, rest) = bytes.split_at(length); *bytes = rest; value
}
fn u32le(bytes: &mut &[u8]) -> u32 {
    u32::from_le_bytes(take(bytes, 4).try_into().unwrap())
}
extern "C" {
    fn g15c_native_thermal_masks(identity: *const u32, enabled: *const u32,
                                 output: *mut [[u32; 4]; 3]) -> i32;
}
fn raw_refusals() {
    // Guard inputs individually and preserve all output bytes on refusal.
    let identity = [0x07040000, 0x0001040a, 0, 0, 0, 0];
    let enabled = [0, 0, 0x802a002b, 0x802a002b];
    for which in 0..4 {
        let mut out = [[0x5a5a5a5a; 4]; 3];
        let id = if which == 0 { core::ptr::null() } else { identity.as_ptr() };
        let zeros = [0; 4];
        let mask = if which == 1 { core::ptr::null() } else if which == 3 { zeros.as_ptr() } else { enabled.as_ptr() };
        let output = if which == 2 { core::ptr::null_mut() } else { &mut out };
        assert_ne!(unsafe { g15c_native_thermal_masks(id, mask, output) }, 0);
        assert_eq!(out, [[0x5a5a5a5a; 4]; 3]);
    }
    for id in [[0; 6], [0x07040000, 0x00010409, 0, 0, 0, 0]] {
        let mut out = [[0xa5a5a5a5; 4]; 3];
        assert_ne!(unsafe { g15c_native_thermal_masks(id.as_ptr(), enabled.as_ptr(), &mut out) }, 0);
        assert_eq!(out, [[0xa5a5a5a5; 4]; 3]);
    }
    for word in 2..4 {
        for bit in 0..32 {
            if enabled[word] & (1 << bit) == 0 { continue; }
            let mut missing = enabled; missing[word] &= !(1 << bit);
            let mut out = [[0x12345678; 4]; 3];
            assert_ne!(unsafe { g15c_native_thermal_masks(identity.as_ptr(), missing.as_ptr(), &mut out) }, 0);
            assert_eq!(out, [[0x12345678; 4]; 3]);
        }
    }
}
fn main() {
    std::panic::set_hook(Box::new(|info| { eprintln!("{info}"); std::process::exit(101); }));
    #[cfg(CONFIG_DRM_ASAHI_G15_NATIVE_POWER)] raw_refusals();
    let mut data = Vec::new(); std::io::stdin().read_to_end(&mut data).unwrap();
    let mut input = data.as_slice(); let count = u32le(&mut input);
    for index in 0..count {
        let valid = u32le(&mut input) != 0;
        let contract = u32le(&mut input);
        let length = u32le(&mut input) as usize;
        let mut identity = [0; 6]; for value in &mut identity { *value = u32le(&mut input); }
        let identity_before = identity;
        let mut expected = [[0; 4]; 3];
        for role in &mut expected { for value in role { *value = u32le(&mut input); } }
        let source = take(&mut input, length); let original = source.to_vec();
        let member = g15::adt::membership(source);
        let firmware = g15::identity::NativeFirmware { build:g15::identity::Build {
            native_board_contract: if contract == 0 { None } else { Some(contract) } } };
        let result = g15::board::native::thermal::select(&firmware, &member, &identity);
        let valid = valid && cfg!(CONFIG_DRM_ASAHI_G15_NATIVE_POWER);
        match result {
            Ok(masks) => { assert!(valid, "case {index}: refusal bypass"); assert_eq!(masks.cells(), &expected, "case {index}: roles"); },
            Err(why) => assert!(!valid, "case {index}: unexpected refusal: {why}"),
        }
        assert_eq!(source, original.as_slice());
        assert_eq!(identity, identity_before);
    }
    assert!(input.is_empty());
    println!("{count} actual thermal pipeline cases; invented ADT/identity, modeled firmware token; no device I/O");
}

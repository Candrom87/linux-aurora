// SPDX-License-Identifier: GPL-2.0-only OR MIT
// Run with: rustc --edition=2021 --test tools/asahi/g15-identity-tests.rs -o /tmp/g15-identity-tests
// /tmp/g15-identity-tests
#![allow(dead_code)]

#[path = "../../drivers/gpu/drm/asahi/g15/identity/matching.rs"]
mod matching;
#[path = "../../drivers/gpu/drm/asahi/g15/identity/lookup.rs"]
mod lookup;

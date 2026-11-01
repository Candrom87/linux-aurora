#!/usr/bin/env python3
"""Exercise the checked-out G15 allocator's preflight with synthetic inputs.

Only the real Rust validation prefix is compiled with minimal host stand-ins.
No kernel, firmware, device, memory mapping, or private machine data is used.
"""
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2] if HERE.parent.name == 'asahi' else HERE.parent / 'linux-aurora'
SOURCE = ROOT / 'drivers/gpu/drm/asahi/g15/object.rs'

source = SOURCE.read_text()
start = source.index('        let page = hw::PAGE as usize;', source.index('pub(super) fn alloc_split('))
end = source.index('        // Populate and DMA-map first:', start)
preflight = source[start:end]

fixture = r'''
#![allow(dead_code, unused_variables)]
type Result<T = ()> = core::result::Result<T, i32>;
const EINVAL: i32 = 22;
const ENOSPC: i32 = 28;
mod hw {
    pub const PAGE: u64 = 0x4000;
    // Synthetic address windows, not a board's firmware or hardware data.
    pub const HOST: u64 = 0xffffff0000000000;
    pub const HOST_SIZE: u64 = 0x10000000;
    pub const USER_RANGE: core::ops::Range<u64> = 0x4000..0x40000000;
    pub mod va { pub const FW_ALLOC: core::ops::Range<u64> = super::HOST + 0x4000000..super::HOST + 0x8000000; }
    pub mod sizes { pub const MAX_CLIENT: usize = 4096; }
}
#[derive(Clone, Copy)] enum Placement { At(u64), Anywhere }
struct Request { size: usize, placement: Placement, offset: usize, client: Option<(u64, u64)>, lower: Option<u64> }
struct Objects { fw_next: u64, clients: Vec<()> }
impl Objects {
    fn preflight(&self, req: Request, prefix: Option<(usize, ())>) -> Result {
PREFLIGHT
        Ok(())
    }
}
fn req(size: usize, placement: Placement) -> Request {
    Request { size, placement, offset: 0, client: None, lower: None }
}
fn objects() -> Objects { Objects { fw_next: hw::va::FW_ALLOC.start, clients: Vec::new() } }
#[test] fn normal_fixed_and_guarded_allocations() {
    let o = objects();
    assert!(o.preflight(req(1, Placement::At(hw::HOST + 0x3fff)), None).is_ok());
    assert!(o.preflight(req(1, Placement::Anywhere), None).is_ok());
    assert!(o.preflight(req(2 * hw::PAGE as usize, Placement::Anywhere), Some((hw::PAGE as usize, ()))).is_ok());
}
#[test] fn host_extent_must_not_wrap() {
    let o = objects();
    let size = 0x100_0000_0000usize;
    assert!(o.preflight(req(size, Placement::At(hw::HOST)), None).is_err());
}
#[test] fn page_rounding_must_not_wrap() {
    assert!(objects().preflight(req(usize::MAX, Placement::Anywhere), None).is_err());
}
#[test] fn client_extent_must_not_wrap() {
    let mut r = req(2 * hw::PAGE as usize, Placement::Anywhere);
    r.client = Some((u64::MAX & !(hw::PAGE - 1), 0));
    assert!(objects().preflight(r, None).is_err());
}
#[test] fn guard_and_alias_boundaries() {
    let mut o = objects();
    o.fw_next = hw::va::FW_ALLOC.end - 2 * hw::PAGE;
    assert!(o.preflight(req(hw::PAGE as usize, Placement::Anywhere), None).is_ok());
    assert_eq!(o.preflight(req(2 * hw::PAGE as usize, Placement::Anywhere), None), Err(ENOSPC));
    let mut r = req(hw::PAGE as usize, Placement::Anywhere);
    r.client = Some((hw::USER_RANGE.end - hw::PAGE, 0));
    assert!(o.preflight(r, None).is_ok());
    let mut r = req(2 * hw::PAGE as usize, Placement::Anywhere);
    r.client = Some((hw::USER_RANGE.end - hw::PAGE, 0));
    assert!(o.preflight(r, None).is_err());
}
'''.replace('PREFLIGHT', preflight)

with tempfile.TemporaryDirectory(prefix='g15-alloc-bounds-') as tmp:
    rust, binary = Path(tmp) / 'fixture.rs', Path(tmp) / 'fixture'
    rust.write_text(fixture)
    for overflow_checks in ('off', 'on'):
        print('overflow checks:', overflow_checks, flush=True)
        subprocess.run([os.environ.get('RUSTC', 'rustc'), '--edition=2021', '--test', '-C',
                        'overflow-checks=' + overflow_checks, str(rust), '-o', str(binary)], check=True)
        subprocess.run([str(binary)], check=True)

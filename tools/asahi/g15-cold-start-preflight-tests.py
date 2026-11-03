#!/usr/bin/env python3
"""Run the actual cold-start block with injected ASC, SGX and handoff failures.

No kernel, registers or firmware are accessed. Optional --before verifies that
the regression rejects a source version that opens SGX before observing RUN
or the TTBAT contents of fully reserved System RAM. Non-RAM ordering is retained.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


FIXTURE = r'''
use std::cell::{Cell,RefCell};
use std::collections::VecDeque;
use std::ops::Range;
type Result<T> = std::result::Result<T, i32>;
const EBUSY: i32 = -16;
mod regs { pub const ASC_RUN: u32 = 16; }
#[derive(Default)]
struct State {
    control: VecDeque<Result<u32>>, events: Vec<&'static str>,
    open_error: Option<i32>, map_error: Option<i32>, clear: VecDeque<Result<bool>>,
    drops:usize,
}
thread_local! {static RAM:Cell<[bool;4]>=const {Cell::new([false;4])};}
mod glue {
    pub fn ram_within(start:u64,size:u64)->bool {
        assert_eq!(size,0x4000);
        let index=match start {0x10000=>0,0x20000=>1,0x30000=>2,0x40000=>3,_=>panic!("bad region")};
        super::RAM.with(|ram|ram.get()[index])
    }
}
struct Adt<'a> {
    state:&'a RefCell<State>, ttbs:Range<u64>,tables:Range<u64>,
    handoff:Range<u64>,shared_l2:Range<u64>,
}
struct Regs<'a>(&'a RefCell<State>);
impl Regs<'_> {
    fn asc_control(&self) -> Result<u32> {
        let mut s = self.0.borrow_mut();
        s.events.push("read");
        s.control.pop_front().expect("unexpected ASC read")
    }
    fn open(&self) -> Result<()> {
        let mut s = self.0.borrow_mut();
        s.events.push("open");
        s.open_error.map_or(Ok(()), Err)
    }
}
mod uat {
    use super::*;
    pub struct Window<'a>(&'a RefCell<State>);
    impl<'a> Window<'a> {
        pub fn new(adt: &Adt<'a>) -> Result<Self> {
            let mut s = adt.state.borrow_mut();
            s.events.push("map");
            match s.map_error { Some(e) => Err(e), None => Ok(Self(adt.state)) }
        }
        pub fn ttbat_is_clear(&self) -> Result<bool> {
            let mut s = self.0.borrow_mut();
            s.events.push("ttbat");
            s.clear.pop_front().expect("unexpected TTBAT read")
        }
    }
    impl Drop for Window<'_> {fn drop(&mut self) {self.0.borrow_mut().drops+=1;}}
}
fn cold(state: &RefCell<State>) -> Result<u32> {
    let regs = Regs(state);
    let adt = Adt {state,ttbs:0x10000..0x14000,tables:0x20000..0x24000,
                   handoff:0x30000..0x34000,shared_l2:0x40000..0x44000};
    START_BLOCK
    let _ = window;
    Ok(control)
}
fn case(reads: &[Result<u32>], open_error: Option<i32>, map_error: Option<i32>,
        clear: Option<Result<bool>>, expected: Result<u32>, events: &[&str]) {
    let state = RefCell::new(State {
        control: reads.iter().copied().collect(), open_error, map_error,
        clear:clear.into_iter().collect(),
        ..State::default()
    });
    assert_eq!(cold(&state), expected);
    assert_eq!(state.borrow().events, events, "cold preflight operation order");
}
fn main() {
    // Non-RAM carveouts retain the original post-open mapping/read order.
    // Negative controls catch the original opening of an already-running ASC.
    case(&[Ok(16)], None, None, None, Err(EBUSY), &["read"]);
    case(&[Err(-5)], None, None, None, Err(-5), &["read"]);
    case(&[Ok(0), Ok(16)], None, None, None, Err(EBUSY), &["read","open","read"]);
    case(&[Ok(0)], Some(-6), None, None, Err(-6), &["read","open"]);
    case(&[Ok(0), Err(-7)], None, None, None, Err(-7), &["read","open","read"]);
    case(&[Ok(0), Ok(0)], None, Some(-12), None, Err(-12), &["read","open","read","map"]);
    case(&[Ok(0), Ok(0)], None, None, Some(Err(-14)), Err(-14), &["read","open","read","map","ttbat"]);
    case(&[Ok(0), Ok(0)], None, None, Some(Ok(false)), Err(EBUSY), &["read","open","read","map","ttbat"]);
    // Preserve the later observed non-RUN control bits for the eventual start.
    case(&[Ok(1), Ok(2)], None, None, Some(Ok(true)), Ok(2), &["read","open","read","map","ttbat"]);
    // Known reserved RAM is examined before SGX, then reused and rechecked.
    ram_case([true;4], &[Ok(0),Ok(0)], None, None, &[Ok(false)], Err(EBUSY),
             &["read","map","ttbat"],1);
    ram_case([true;4], &[Ok(0)], None, None, &[Err(-14)], Err(-14),
             &["read","map","ttbat"],1);
    ram_case([true;4], &[Ok(0)], None, Some(-12), &[], Err(-12),
             &["read","map"],0);
    ram_case([true;4], &[Ok(16)], None, None, &[], Err(EBUSY), &["read"],0);
    ram_case([true;4], &[Err(-5)], None, None, &[], Err(-5), &["read"],0);
    ram_case([true;4], &[Ok(0)], Some(-6), None, &[Ok(true)], Err(-6),
             &["read","map","ttbat","open"],1);
    ram_case([true;4], &[Ok(0),Ok(16)], None, None, &[Ok(true)], Err(EBUSY),
             &["read","map","ttbat","open","read"],1);
    ram_case([true;4], &[Ok(0),Err(-7)], None, None, &[Ok(true)], Err(-7),
             &["read","map","ttbat","open","read"],1);
    ram_case([true;4], &[Ok(0),Ok(0)], None, None, &[Ok(true),Ok(false)], Err(EBUSY),
             &["read","map","ttbat","open","read","ttbat"],1);
    ram_case([true;4], &[Ok(0),Ok(0)], None, None, &[Ok(true),Err(-14)], Err(-14),
             &["read","map","ttbat","open","read","ttbat"],1);
    ram_case([true;4], &[Ok(1),Ok(2)], None, None, &[Ok(true),Ok(true)], Ok(2),
             &["read","map","ttbat","open","read","ttbat"],1);
    for index in 0..4 {
        let mut mixed=[true;4];mixed[index]=false;
        ram_case(mixed,&[Ok(0),Ok(0)],None,None,&[Ok(false)],Err(EBUSY),
                 &["read","open","read","map","ttbat"],1);
    }
    println!("24 cold-start preflight scenarios passed; no hardware accessed");
}

fn ram_case(ram:[bool;4],reads:&[Result<u32>],open_error:Option<i32>,map_error:Option<i32>,
            clear:&[Result<bool>],expected:Result<u32>,events:&[&str],drops:usize) {
    RAM.with(|value|value.set(ram));
    let state=RefCell::new(State {control:reads.iter().copied().collect(),open_error,map_error,
                                clear:clear.iter().copied().collect(),..State::default()});
    assert_eq!(cold(&state),expected);
    assert_eq!(state.borrow().events,events,"cold preflight operation order");
    assert_eq!(state.borrow().drops,drops,"window lifetime");
}
'''


def block(path):
    source = path.read_text()
    start = source.index('pub(crate) fn start(')
    opening = source.index('    regs.open()?;', start)
    preflight = source.find('    // ASC is readable before opening SGX', start, opening)
    return source[preflight if preflight >= 0 else opening:
                  source.index('    // Adopt the UAT', opening)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='G15 boot.rs to test')
    parser.add_argument('--rustc', default='rustc')
    parser.add_argument('--before', type=Path, help='old source expected to fail')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='g15-cold-preflight-') as tmp:
        tmp = Path(tmp)
        for name, source, expect_success in [('current', args.source, True)] + (
                [('before', args.before, False)] if args.before else []):
            path, binary = tmp / (name + '.rs'), tmp / name
            path.write_text(FIXTURE.replace('START_BLOCK', block(source)))
            subprocess.run([args.rustc, '--edition=2021', str(path), '-o', str(binary)],
                           check=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            if (result.returncode == 0) != expect_success:
                raise RuntimeError(name + ': unexpected result\n' + result.stdout + result.stderr)
            if not expect_success and 'cold preflight operation order' not in result.stderr:
                raise RuntimeError(name + ': failed outside the intended ordering assertion\n' + result.stderr)
            print(result.stdout.strip() if expect_success else
                  'Before-change negative control failed as expected')


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Exercise G15 receive-index handling with synthetic ring state only."""
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2] if HERE.parent.name == 'asahi' else HERE.parent / 'linux-aurora'
source = (ROOT / 'drivers/gpu/drm/asahi/g15/channel.rs').read_text()
for call in ('Self::drain_one(&self.log, i * 0x30, RING_SLOTS)?',
             'Self::drain_one(&self.trace, 0, 2 * RING_SLOTS)?',
             'Self::drain_one(&self.stats, 0, RING_SLOTS)'):
    if call not in source:
        raise ValueError('unexpected receive-ring capacity at call site')
start = source.index('    fn drain_one(')
opening = source.index('{', start)
depth = 0
for i in range(opening, len(source)):
    if source[i] == '{':
        depth += 1
    elif source[i] == '}':
        depth -= 1
        if not depth:
            actual = source[start:i + 1]
            break
else:
    raise ValueError('unterminated function')

fixture = r'''
use std::cell::{Cell, RefCell};
type Result<T=()> = std::result::Result<T, i32>;
const EIO:i32=5;
const RING_SLOTS:u32=256;
const STATE_READ:usize=0;
const STATE_WRITE:usize=0x20;
thread_local! { static BARRIERS:Cell<usize>=const { Cell::new(0) }; }
fn wmb() { BARRIERS.with(|x| x.set(x.get()+1)); }
struct State { words:RefCell<[u32;72]>, writes:RefCell<Vec<(usize,u32)>> }
impl Default for State { fn default()->Self { Self {words:RefCell::new([0;72]),writes:RefCell::new(vec![])} } }
impl State {
    fn r32(&self, off:usize)->Result<u32> { Ok(self.words.borrow()[off/4]) }
    fn w32(&self, off:usize, val:u32)->Result { self.words.borrow_mut()[off/4]=val; self.writes.borrow_mut().push((off,val)); Ok(()) }
}
struct Ring { state:State }
struct Channels;
impl Channels { ACTUAL }
fn ring(read:u32, producer:u32, offset:usize)->Ring {
    let state=State::default();
    state.words.borrow_mut()[(offset+STATE_READ)/4]=read;
    state.words.borrow_mut()[(offset+STATE_WRITE)/4]=producer;
    Ring {state}
}
#[test] fn valid_indices_and_wrap_are_acknowledged() {
    for (read,producer) in [(1,2),(255,0),(0,255)] {
        BARRIERS.with(|x| x.set(0));
        let r=ring(read,producer,0x30);
        assert_eq!(Channels::drain_one(&r,0x30,RING_SLOTS),Ok(()));
        assert_eq!(*r.state.writes.borrow(),vec![(0x30,producer)]);
        BARRIERS.with(|x| assert_eq!(x.get(),1));
    }
}
#[test] fn equal_valid_indices_do_not_write() {
    let r=ring(255,255,0);
    assert_eq!(Channels::drain_one(&r,0,RING_SLOTS),Ok(()));
    assert!(r.state.writes.borrow().is_empty());
}
#[test] fn out_of_range_indices_are_not_acknowledged() {
    for (read,producer) in [(0,256),(256,0),(256,256),(1,u32::MAX)] {
        BARRIERS.with(|x| x.set(0));
        let r=ring(read,producer,0);
        assert_eq!(Channels::drain_one(&r,0,RING_SLOTS),Err(EIO));
        assert!(r.state.writes.borrow().is_empty());
        BARRIERS.with(|x| assert_eq!(x.get(),0));
    }
}
#[test] fn trace_accepts_indices_above_255_and_refuses_512() {
    for (read,producer) in [(255,256),(511,0),(0,511)] {
        let r=ring(read,producer,0);
        assert_eq!(Channels::drain_one(&r,0,2*RING_SLOTS),Ok(()));
        assert_eq!(*r.state.writes.borrow(),vec![(0,producer)]);
    }
    let r=ring(0,512,0);
    assert_eq!(Channels::drain_one(&r,0,2*RING_SLOTS),Err(EIO));
    assert!(r.state.writes.borrow().is_empty());
}
'''.replace('ACTUAL', actual)

with tempfile.TemporaryDirectory(prefix='g15-receive-indices-') as tmp:
    rust, binary = Path(tmp) / 'test.rs', Path(tmp) / 'test'
    rust.write_text(fixture)
    subprocess.run([os.environ.get('RUSTC', 'rustc'), '--edition=2021', '--test',
                    str(rust), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

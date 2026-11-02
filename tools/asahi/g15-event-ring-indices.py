#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Exercise the G15 event-drain implementation using synthetic ring state."""
import os
from pathlib import Path
import subprocess
import tempfile

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2] if HERE.parent.name == 'asahi' else HERE.parent / 'linux-aurora'
source = (ROOT / 'drivers/gpu/drm/asahi/g15/event.rs').read_text()
start = source.index('    pub(crate) fn drain(')
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
#![allow(dead_code,unused_variables)]
use std::cell::{Cell,RefCell};
type Result<T=()> = std::result::Result<T,i32>;
const EIO:i32=5;
const GFP_KERNEL:()=();
const RING_SLOTS:u32=256;
const STATE_READ:usize=0;
const STATE_WRITE:usize=0x20;
const GROW_QUEUE:usize=8;
macro_rules! dev_err { ($($t:tt)*) => {{}} }
macro_rules! dev_warn { ($($t:tt)*) => {{}} }
macro_rules! dev_dbg { ($($t:tt)*) => {{}} }
mod device { pub struct Device; }
mod bindings { pub unsafe fn ktime_get()->i64 { 42 } }
mod event { pub const STAGE:u32=1; pub const FAULT:u32=0; pub const TIMEOUT:u32=4; pub const PB_GROW:u32=7; pub const CHANNEL_ERROR:u32=8; }
thread_local! { static BARRIERS:Cell<usize>=const { Cell::new(0) }; }
fn rmb(){BARRIERS.with(|x|x.set(x.get()+1));}
fn mb(){BARRIERS.with(|x|x.set(x.get()+1));}
fn wmb(){BARRIERS.with(|x|x.set(x.get()+1));}
struct State {read:u32,producer:u32,writes:RefCell<Vec<(usize,u32)>>}
impl State {
    fn r32(&self,off:usize)->Result<u32> {if off==STATE_READ {Ok(self.read)} else {Ok(self.producer)}}
    fn w32(&self,off:usize,value:u32)->Result {self.writes.borrow_mut().push((off,value));Ok(())}
}
struct Ring {state:State,words:Cell<usize>}
#[derive(Clone,Copy)] struct ErrorEvent {kind:u32,raw:[u32;14],slot:i32}
struct Grow(Vec<[u32;14]>);
impl Grow {
    fn len(&self)->usize {self.0.len()}
    fn push(&mut self,w:[u32;14],_:())->Result {self.0.push(w);Ok(())}
}
struct Events {compute_events:u32,stage_events:u32,errors:u32,last_error:Option<ErrorEvent>,channel_errors:u32,event_ns:[u64;3],grow:Grow,grow_lost:u32}
impl Events {
    fn new()->Self {Self {compute_events:0,stage_events:0,errors:0,last_error:None,channel_errors:0,event_ns:[0;3],grow:Grow(vec![]),grow_lost:0}}
    fn words(ring:&Ring,_:u32)->Result<[u32;14]> {ring.words.set(ring.words.get()+1);let mut w=[0;14];w[0]=event::STAGE;w[1]=4;Ok(w)}
    ACTUAL
}
fn ring(read:u32,producer:u32)->Ring {Ring {state:State {read,producer,writes:RefCell::new(vec![])},words:Cell::new(0)}}
#[test] fn invalid_indices_report_failure_without_acknowledging() {
    for (read,producer) in [(0,256),(256,0),(256,256),(0,u32::MAX),
                            (u32::MAX,0),(u32::MAX,u32::MAX)] {
        BARRIERS.with(|x|x.set(0)); let r=ring(read,producer);let mut e=Events::new();
        assert_eq!(e.drain(&device::Device,&r),Err(EIO));
        assert_eq!(r.words.get(),0);assert!(r.state.writes.borrow().is_empty());
        BARRIERS.with(|x|assert_eq!(x.get(),0));
    }
}
#[test] fn valid_event_wrap_is_decoded_before_ack() {
    BARRIERS.with(|x|x.set(0));let r=ring(255,0);let mut e=Events::new();
    assert_eq!(e.drain(&device::Device,&r),Ok(()));assert_eq!(r.words.get(),1);
    assert_eq!(e.compute_events,1);assert_eq!(e.stage_events,1);
    assert_eq!(*r.state.writes.borrow(),vec![(STATE_READ,0)]);
    BARRIERS.with(|x|assert_eq!(x.get(),3));
}
'''.replace('ACTUAL', actual)

with tempfile.TemporaryDirectory(prefix='g15-event-indices-') as tmp:
    rust, binary = Path(tmp) / 'test.rs', Path(tmp) / 'test'
    rust.write_text(fixture)
    subprocess.run([os.environ.get('RUSTC', 'rustc'), '--edition=2021', '--test',
                    str(rust), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

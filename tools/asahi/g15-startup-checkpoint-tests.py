#!/usr/bin/env python3
"""Inject latched transport errors into actual G15 startup source blocks.

Compiles source with fake register/RTKit/UAT objects only. No driver, firmware,
or hardware is accessed. --before checks the original missing-check regression.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


FIXTURE = r'''
#![allow(dead_code,unused_variables)]
use std::cell::RefCell;
use std::rc::Rc;
type Result<T=()> = std::result::Result<T,i32>;
const ENODEV:i32=-19;
const LATCHED:i32=-5;
const STAGE_ERROR:i32=-12;
struct State {
    events:Vec<&'static str>, latched:bool,
    latch_at:Option<&'static str>, fail_at:Option<&'static str>, endpoints:u8,
}
type Shared=Rc<RefCell<State>>;
thread_local! { static ACTIVE:RefCell<Option<Shared>>=const {RefCell::new(None)}; }
fn stage(s:&Shared,name:&'static str)->Result {
    let mut s=s.borrow_mut(); s.events.push(name);
    if s.latch_at==Some(name) {s.latched=true;}
    if s.fail_at==Some(name) {Err(STAGE_ERROR)} else {Ok(())}
}
#[derive(Clone)] struct Device(Shared);
struct Config;
struct BoardImages;
struct InitData;
struct Channels;
struct Events(Shared);
impl Events {fn reserve(&mut self)->Result {stage(&self.0,"reserve")}}
mod hw {pub mod ep {pub const FIRMWARE:u8=32;pub const DOORBELL:u8=33;}}
mod regs {pub const ASC_RUN:u32=16;}
struct Regs(Shared);
impl Regs {fn set_asc_control(&self,value:u32)->Result {
    assert_eq!(value,17); stage(&self.0,"asc")
}}
struct Rtkit(Shared);
impl Rtkit {
    fn wake(&mut self)->Result {stage(&self.0,"wake")}
    fn has_endpoint(&self,ep:u8)->bool {
        let bit=if ep==hw::ep::FIRMWARE {1} else {2};
        self.0.borrow().endpoints & bit !=0
    }
    fn start_endpoint(&mut self,ep:u8)->Result {
        stage(&self.0,if ep==hw::ep::FIRMWARE {"firmware_ep"} else {"doorbell_ep"})
    }
}
struct Session {dev:Device, rtkit:Rtkit, regs:Regs, uat:Shared, adt:()}
impl Session {fn check(&self)->Result {
    if self.uat.borrow().latched {Err(LATCHED)} else {Ok(())}
}}
struct Window(Shared);
impl Window {fn bootstrap(&mut self,_:&Device,_:&Shared)->Result {stage(&self.0,"bootstrap")}}
struct Objects {window:Window}
mod initdata {
    use super::*;
    fn stage_data(objects:&Objects)->Result {stage(&objects.window.0,"build")}
    pub fn build(_:&Device,_:&Config,_:&(),objects:&mut Objects,_:BoardImages)
        ->Result<(InitData,Channels,u32)> {
        stage_data(objects)?; Ok((InitData,Channels,42))
    }
}
mod uat {pub fn tlbi_all() {
    super::ACTIVE.with(|s|super::stage(s.borrow().as_ref().unwrap(),"tlbi").unwrap());
}}
fn run(session:&mut Session,cfg:&Config,objects:&mut Objects,events:&mut Events,
       built:&mut Option<(InitData,Channels,u32)>,board_images:BoardImages)->Result {
    RUN_PREFIX
    Ok(())
}
fn start(mut session:&mut Session,mut events:&mut Events,mut objects:&mut Objects)->Result {
    let control=1;
    let cfg=&Config;
    let mut built=None;
    let board_images=BoardImages;
    START_CHAIN
    result
}
fn case(label:&str,latched:bool,latch_at:Option<&'static str>,fail_at:Option<&'static str>,
        endpoints:u8,expected:Result,events:&[&str]) {
    let state=Rc::new(RefCell::new(State{events:vec![],latched,latch_at,fail_at,endpoints}));
    ACTIVE.with(|s|*s.borrow_mut()=Some(state.clone()));
    let mut session=Session{dev:Device(state.clone()),rtkit:Rtkit(state.clone()),
        regs:Regs(state.clone()),uat:state.clone(),adt:()};
    let mut events_arg=Events(state.clone());
    let mut objects=Objects{window:Window(state.clone())};
    let result=start(&mut session,&mut events_arg,&mut objects);
    assert_eq!(result,expected,"{label}: known latched error must stop the next stage");
    assert_eq!(state.borrow().events,events,"{label}: known latched error must stop the next stage");
    ACTIVE.with(|s|*s.borrow_mut()=None);
}
fn main() {
    let stages=["reserve","asc","wake","firmware_ep","doorbell_ep","bootstrap","build","tlbi"];
    case("normal",false,None,None,3,Ok(()),&stages);
    case("already latched",true,None,None,3,Err(LATCHED),&stages[..1]);
    for (n,at) in stages[..7].iter().enumerate() {
        case("latch during completed stage",false,Some(at),None,3,Err(LATCHED),&stages[..n+1]);
        case("stage failure",false,None,Some(at),3,Err(STAGE_ERROR),&stages[..n+1]);
    }
    for mask in [1,2] {case("missing endpoint",false,None,None,mask,Err(ENODEV),&stages[..3]);}
    println!("18 actual-source startup checkpoint cases passed; no hardware accessed");
}
'''


def blocks(path):
    source = path.read_text()
    start = source.index('fn run(')
    prefix = source[source.index('    let dev = session.dev.clone();', start):
                    source.index('    dev_info!(', start)]
    start = source.index('pub(crate) fn start(')
    chain = source[source.index('    let result = events', start):
                   source.index('    if let Err(e) = result', start)]
    # New early errors still use the existing retained-session handler.
    handler = source[source.index('    if let Err(e) = result', start):
                     source.index('    let (init, channels, max_frequency_khz)', start)]
    if 'shared.orphan(Orphan {' not in handler or 'return Err(e);' not in handler:
        raise ValueError('retained-session error handling changed; review before extracting')
    return prefix, chain


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path, help='current G15 boot.rs')
    p.add_argument('--before', type=Path, help='old boot.rs expected to fail')
    p.add_argument('--rustc', default='rustc')
    args = p.parse_args()
    versions = [('current', args.source, True)]
    if args.before:
        versions.append(('before', args.before, False))
    with tempfile.TemporaryDirectory(prefix='g15-startup-checkpoints-') as tmp:
        tmp = Path(tmp)
        for name, source, expected in versions:
            prefix, chain = blocks(source)
            path, binary = tmp / (name + '.rs'), tmp / name
            path.write_text(FIXTURE.replace('RUN_PREFIX', prefix).replace('START_CHAIN', chain))
            subprocess.run([args.rustc, '--edition=2021', str(path), '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            if expected:
                if result.returncode:
                    raise RuntimeError(result.stdout + result.stderr)
                print(result.stdout.strip())
            elif result.returncode == 0 or 'known latched error must stop the next stage' not in result.stderr:
                raise AssertionError('old source did not fail the intended regression')
            else:
                print('Old source continued past a known latched error; negative control failed as expected')


if __name__ == '__main__':
    main()

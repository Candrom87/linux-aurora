#!/usr/bin/env python3
"""Exercise actual G15 control posting and Session::send with fake firmware."""
import os
from pathlib import Path
import subprocess
import tempfile

G15=Path(__file__).resolve().parents[2]/'drivers/gpu/drm/asahi/g15'
def function(source,signature):
    start=source.index(signature);opening=source.index('{',start);depth=0
    for i in range(opening,len(source)):
        if source[i]=='{': depth+=1
        elif source[i]=='}':
            depth-=1
            if not depth:return source[start:i+1]
    raise ValueError('unterminated function')

channel=(G15/'channel.rs').read_text()
boot=(G15/'boot.rs').read_text()
session=boot[boot.index('impl Session {'):]
fixture=r'''
#![allow(dead_code,unused_variables)]
use std::cell::Cell; use std::marker::PhantomData; use std::rc::Rc;
type Result<T=()> = std::result::Result<T,i32>;
const EIO:i32=5;const EBUSY:i32=16;const ETIMEDOUT:i32=110;
const RING_SLOTS:u32=256;const STATE_WRITE:usize=0;
macro_rules! dev_err {($($t:tt)*)=>{{}}}
mod rings {pub const DEVCTL_RECORD:usize=56;}
mod hw {pub mod ep {pub const DOORBELL:u8=33;}pub mod engine {pub const DEVCTL_DOORBELL:u64=17;}}
#[derive(Clone)]struct Dev;
struct Delta; impl Delta {fn from_millis(x:u64)->u64{x}fn from_micros(x:u64)->u64{x}}
struct Monotonic;struct Instant<T>(PhantomData<T>);
impl<T> Instant<T> {fn now()->Self{Self(PhantomData)}fn elapsed(&self)->u64{0}}
fn fsleep(_:u64){} fn wmb(){}
#[derive(Default)]struct Fake {crashed:Cell<bool>,halted:Cell<bool>,halt_read_error:Cell<bool>,crash_on_read:Cell<bool>,crash_on_write:Cell<bool>,crash_on_drain:Cell<bool>,drain_advances:Cell<bool>,auto_consume:Cell<bool>,read_value:Cell<u32>,reads:Cell<usize>,ring_writes:Cell<usize>,index_writes:Cell<usize>,sends:Cell<usize>}
struct Rtkit(Rc<Fake>); impl Rtkit {fn send(&mut self,_:u8,_:u64)->Result{self.0.sends.set(self.0.sends.get()+1);Ok(())}}
struct Session {dev:Dev,rtkit:Rtkit}
impl Session {
 fn check(&self)->Result {if self.rtkit.0.crashed.get(){Err(EIO)}else{Ok(())}}
 SEND
}
struct Ring(Rc<Fake>);impl Ring {fn write(&self,_:usize,_:&[u8])->Result{self.0.ring_writes.set(self.0.ring_writes.get()+1);Ok(())}}
struct State(Rc<Fake>);impl State {fn w32(&self,_:usize,value:u32)->Result{self.0.index_writes.set(self.0.index_writes.get()+1);if self.0.crash_on_write.get(){self.0.crashed.set(true)}if self.0.auto_consume.get(){self.0.read_value.set(value)}Ok(())}}
struct Queue {fake:Rc<Fake>,ring:Ring,state:State}
impl Queue {
 fn read(&self)->Result<u32>{self.fake.reads.set(self.fake.reads.get()+1);if self.fake.crash_on_read.get(){self.fake.crashed.set(true)}Ok(self.fake.read_value.get())}
 fn completion(&self)->Result<u32>{Ok(self.fake.read_value.get())}
}
struct Channels {devctl_producer:u32,devctl:Queue}
impl Channels {fn drain_rings(&mut self,_:&Dev,_:&mut Events)->Result{
 if self.devctl.fake.crash_on_drain.get(){self.devctl.fake.crashed.set(true)}
 if self.devctl.fake.drain_advances.get(){self.devctl.fake.read_value.set(self.devctl_producer)}Ok(())}}
struct Events;struct InitData(Rc<Fake>);impl InitData {fn halted(&self)->Result<bool>{if self.0.halt_read_error.get(){Err(EIO)}else{Ok(self.0.halted.get())}}}
struct DevCtl;impl DevCtl {fn opcode(&self)->u32{1}fn to_bytes(&self)->[u8;56]{[0;56]}}
ACTUAL
fn context()->(Rc<Fake>,Session,Channels,Events,InitData){let f=Rc::new(Fake::default());
 (f.clone(),Session{dev:Dev,rtkit:Rtkit(f.clone())},Channels{devctl_producer:0,
 devctl:Queue{fake:f.clone(),ring:Ring(f.clone()),state:State(f.clone())}},Events,InitData(f))}
#[test]fn already_crashed_is_refused_before_read_or_post(){let(f,mut s,mut c,mut e,i)=context();f.crashed.set(true);
 assert_eq!(devctl_submit(&mut s,&mut c,&mut e,&i,&DevCtl,false),Err(EIO));
 assert_eq!((f.reads.get(),f.ring_writes.get(),f.index_writes.get(),f.sends.get()),(0,0,0,0));}
#[test]fn halted_empty_ring_is_not_posted(){let(f,mut s,mut c,mut e,i)=context();f.halted.set(true);
 assert_eq!(devctl_submit(&mut s,&mut c,&mut e,&i,&DevCtl,false),Err(EBUSY));
 assert_eq!((f.ring_writes.get(),f.index_writes.get(),f.sends.get()),(0,0,0));}
#[test]fn halt_status_read_failure_is_not_posted(){let(f,mut s,mut c,mut e,i)=context();f.halt_read_error.set(true);
 assert_eq!(devctl_submit(&mut s,&mut c,&mut e,&i,&DevCtl,false),Err(EIO));
 assert_eq!((f.ring_writes.get(),f.index_writes.get(),f.sends.get()),(0,0,0));}
#[test]fn crashed_session_refuses_mailbox_send(){let(f,mut s,_,_,_)=context();f.crashed.set(true);
 assert_eq!(s.send(1,2),Err(EIO));assert_eq!(f.sends.get(),0);}
#[test]fn crash_while_checking_empty_ring_is_refused_before_post(){let(f,mut s,mut c,mut e,i)=context();f.crash_on_read.set(true);
 assert_eq!(devctl_submit(&mut s,&mut c,&mut e,&i,&DevCtl,false),Err(EIO));
 assert_eq!((f.ring_writes.get(),f.index_writes.get(),f.sends.get()),(0,0,0));}
#[test]fn crash_after_index_write_does_not_send_but_reports_uncertainty(){let(f,mut s,mut c,mut e,i)=context();f.crash_on_write.set(true);
 assert_eq!(devctl_submit(&mut s,&mut c,&mut e,&i,&DevCtl,false),Err(EIO));
 assert_eq!((f.ring_writes.get(),f.index_writes.get(),f.sends.get(),c.devctl_producer),(1,1,0,1));}
#[test]fn healthy_empty_ring_posts_normally(){let(f,mut s,mut c,mut e,i)=context();
 assert_eq!(devctl_submit(&mut s,&mut c,&mut e,&i,&DevCtl,false),Ok(()));
 assert_eq!((f.ring_writes.get(),f.index_writes.get(),f.sends.get(),c.devctl_producer),(1,1,1,1));}
#[test]fn healthy_post_with_wait_observes_completion(){let(f,mut s,mut c,mut e,i)=context();f.auto_consume.set(true);
 assert_eq!(devctl_submit(&mut s,&mut c,&mut e,&i,&DevCtl,true),Ok(()));
 assert_eq!((f.ring_writes.get(),f.index_writes.get(),f.sends.get(),c.devctl_producer),(1,1,1,1));}
#[test]fn previously_pending_command_is_consumed_before_new_post(){let(f,mut s,mut c,mut e,i)=context();c.devctl_producer=1;f.drain_advances.set(true);
 assert_eq!(devctl_submit(&mut s,&mut c,&mut e,&i,&DevCtl,false),Ok(()));
 assert_eq!((f.ring_writes.get(),f.index_writes.get(),f.sends.get(),c.devctl_producer),(1,1,1,2));}
#[test]fn crash_while_waiting_for_prior_command_does_not_post(){let(f,mut s,mut c,mut e,i)=context();c.devctl_producer=1;f.crash_on_drain.set(true);
 assert_eq!(devctl_submit(&mut s,&mut c,&mut e,&i,&DevCtl,false),Err(EIO));
 assert_eq!((f.ring_writes.get(),f.index_writes.get(),f.sends.get(),c.devctl_producer),(0,0,0,1));}
'''.replace('ACTUAL',function(channel,'pub(crate) fn devctl_submit(')).replace(
    'SEND',function(session,'    pub(crate) fn send('))
with tempfile.TemporaryDirectory(prefix='g15-devctl-preflight-') as tmp:
    source,binary=Path(tmp)/'test.rs',Path(tmp)/'test';source.write_text(fixture)
    subprocess.run([os.environ.get('RUSTC','rustc'),'--edition=2021','--test',str(source),'-o',str(binary)],check=True)
    subprocess.run([str(binary),'--test-threads=1'],check=True)

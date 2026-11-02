#!/usr/bin/env python3
"""Exercise Asahi's actual scatter-map loop with fake page tables and segments."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

here = Path(__file__).resolve()
workspace = next((p for p in here.parents if (p/'linux-aurora/drivers/gpu/drm/asahi/mmu.rs').is_file()), None)
kernel = (workspace/'linux-aurora' if workspace else
          next((p for p in here.parents if (p/'drivers/gpu/drm/asahi/mmu.rs').is_file()), None))
if kernel is None:
    raise SystemExit('cannot find Linux source tree')
source = (kernel/'drivers/gpu/drm/asahi/mmu.rs').read_text()
start = source.index('        let (map_addr, map_length) = (op.addr(), op.length() as usize);')
end = source.index('        let gpuva = match ctx.preallocated_gpuva()', start)
loop = source[start:end]
signature = '    fn covers(&self, mut offset: u64, size: u64, repeat: bool) -> bool {'
if source.count(signature) != 1:
    raise SystemExit('segment coverage helper changed; re-audit')
begin = source.index(signature)
depth = 0
for pos in range(source.index('{', begin), len(source)):
    if source[pos] == '{': depth += 1
    elif source[pos] == '}':
        depth -= 1
        if not depth: break
else:
    raise SystemExit('unterminated coverage helper')
coverage = source[begin:pos+1]
bind = (kernel/'drivers/gpu/drm/asahi/mmu/bind.rs').read_text()
if '!vm_bo.data().segments.lock().as_ref().ok_or(EFAULT)?.covers(offset, size, single_page)' not in bind:
    raise SystemExit('prepared bind no longer calls the coverage helper; re-audit')
harness = r'''
#![allow(dead_code,unused_variables,unused_assignments)]
use std::cell::{Ref,RefCell};
use std::ops::Range;
type Result<T=()> = std::result::Result<T,i32>;
type PhysicalAddr = u64;
const EFAULT:i32=14; const EINVAL:i32=22; const ENODEV:i32=19; const UAT_PGSZ:usize=0x4000;
const UAT_PGMSK:usize=UAT_PGSZ-1;
macro_rules! mod_dev_dbg {($($x:tt)*)=>{}}
mod gpuvm { pub enum GpuVaFlag { REPEAT } }
struct Flags(bool);
impl Flags { fn contains(&self,_:gpuvm::GpuVaFlag)->bool {self.0} }
struct Lock(RefCell<Option<Vec<(u64,u64)>>>);
impl Lock { fn lock(&self)->Ref<'_,Option<Vec<(u64,u64)>>> {self.0.borrow()} }
struct Data { segments:Lock }
struct Bo { data:Data }
impl Bo { fn data(&self)->&Data {&self.data} }
struct SgSegments(Vec<(u64,u64)>);
impl SgSegments {
 fn iter(&self)->std::slice::Iter<'_,(u64,u64)> {self.0.iter()}
ACTUAL_COVERAGE
}
struct Op {addr:u64,len:u64,offset:u64,repeat:bool,bo:Bo}
impl Op {
  fn addr(&self)->u64 {self.addr} fn length(&self)->u64 {self.len}
  fn gem_offset(&self)->u64 {self.offset} fn flags(&self)->Flags {Flags(self.repeat)}
  fn vm_bo(&self)->&Bo {&self.bo}
}
struct Ctx {dev:Option<()>,prot:()}
#[derive(Default)] struct Table {maps:Vec<(Range<u64>,u64,bool)>,fail:bool}
impl Table {
 fn map_pages(&mut self,r:Range<u64>,p:u64,_:(),repeat:bool)->Result {
    if (r.start|r.end|p) & UAT_PGMSK as u64 != 0 {return Err(EINVAL)}
    self.maps.push((r,p,repeat)); if self.fail {Err(EFAULT)} else {Ok(())}
 }
}
#[derive(Default)] struct Vm {page_table:Table,rollbacks:usize}
impl Vm {
 fn rollback_failed_map(&mut self,_:u64,_:u64) {self.rollbacks+=1;self.page_table.maps.clear();}
 fn run(&mut self,op:&Op,ctx:&Ctx)->Result {
ACTUAL_LOOP
  Ok(())
 }
}
fn op(segs:&[(u64,u64)],len:u64,offset:u64,repeat:bool)->Op {
 Op{addr:0x10000,len,offset,repeat,bo:Bo{data:Data{segments:Lock(RefCell::new(Some(segs.to_vec())))}}}
}
fn check(segs:&[(u64,u64)],len:u64,offset:u64,repeat:bool,expected:Result,maps:usize,rollbacks:usize) {
 let mut vm=Vm::default();
 assert_eq!(vm.run(&op(segs,len,offset,repeat),&Ctx{dev:Some(()),prot:()}),expected);
 assert_eq!(vm.page_table.maps.len(),maps); assert_eq!(vm.rollbacks,rollbacks);
}
fn main() {
 let p=UAT_PGSZ as u64;
 for (segs,offset,size,repeat,expected) in [
    (vec![],0,p,false,false), (vec![(p,p)],0,2*p,false,false),
    (vec![(p,p),(3*p,p)],0,2*p,false,true),
    (vec![(p,p),(3*p,p)],p,p,false,true), (vec![(p,p)],p,p,false,false),
    (vec![(p,p/2),(3*p,p)],0,2*p,true,false),
    (vec![(p,p)],0,2*p,true,true), (vec![(p,p)],0,0,true,false),
    (vec![(p,u64::MAX)],u64::MAX-1,1,false,false),
    (vec![(p+1,p)],0,p,false,false), (vec![(p,p/2),(3*p,p)],0,p,false,false),
    (vec![(u64::MAX-p+1,2*p)],p,p,false,false),
    (vec![(u64::MAX-p+1,p)],0,p,false,false),
    (vec![(u64::MAX-p+1,p)],0,p,true,false),
 ] {assert_eq!(SgSegments(segs).covers(offset,size,repeat),expected);}
 // Compare the real preflight and mapping loop on a range of synthetic
 // segment shapes. The fake table checks alignment but does not map RAM.
 for first in [0,p/2,p,2*p] { for second in [0,p/2,p,2*p] {
  let segs=vec![(p,first),(4*p,second)];
  for offset in [0,p,2*p] { for size in [p,2*p,3*p] { for repeat in [false,true] {
   let preflight=SgSegments(segs.clone()).covers(offset,size,repeat);
   let mut vm=Vm::default();
   let admitted=vm.run(&op(&segs,size,offset,repeat),&Ctx{dev:Some(()),prot:()}).is_ok();
   assert_eq!(preflight,admitted,"first={first} second={second} offset={offset} size={size} repeat={repeat}");
  } } }
 } }
 check(&[],p,0,false,Err(EFAULT),0,1);
 check(&[(p,p)],2*p,0,false,Err(EFAULT),0,1);
 check(&[(p,p),(3*p,p)],2*p,0,false,Ok(()),2,0);
 check(&[(p,p),(3*p,p)],p,p,false,Ok(()),1,0);
 check(&[(p,p)],p,p,false,Err(EFAULT),0,1);
 check(&[(p,p/2)],2*p,0,true,Err(EFAULT),0,1);
 check(&[(p,p)],2*p,0,true,Ok(()),1,0);
 let mut vm=Vm::default(); vm.page_table.fail=true;
 assert_eq!(vm.run(&op(&[(p,p)],p,0,false),&Ctx{dev:Some(()),prot:()}),Err(EFAULT));
 assert_eq!(vm.rollbacks,1); assert!(vm.page_table.maps.is_empty());
 println!("actual scatter-map loop: short, offset, repeat, success and rollback cases passed");
}
'''.replace('ACTUAL_LOOP',loop).replace('ACTUAL_COVERAGE',coverage)
env = dict(os.environ)
if workspace and (workspace/'build/rust-toolchain/usr/bin/rustc').is_file():
    tc = workspace/'build/rust-toolchain/usr'
    env['PATH'] = str(tc/'bin')+':'+env.get('PATH','')
    env['LD_LIBRARY_PATH'] = str(tc/'lib')+':'+env.get('LD_LIBRARY_PATH','')
rustc = shutil.which('rustc', path=env.get('PATH'))
if not rustc:
    raise SystemExit('rustc not found')
with tempfile.TemporaryDirectory(prefix='asahi-short-sg-') as tmp:
    rs, binary = Path(tmp)/'test.rs', Path(tmp)/'test'
    rs.write_text(harness)
    subprocess.run([rustc,'--edition=2021',str(rs),'-o',str(binary)],
                   check=True,env=env,timeout=60)
    subprocess.run([str(binary)],check=True,env=env,timeout=10)

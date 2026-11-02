#!/usr/bin/env python3
"""Run actual prepared-bind preflight with synthetic DMA and GPUVM objects."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

here = Path(__file__).resolve()
workspace = next((p for p in here.parents if (p/'linux-aurora/drivers/gpu/drm/asahi/mmu.rs').is_file()), None)
kernel = (workspace/'linux-aurora' if workspace else
          next((p for p in here.parents if (p/'drivers/gpu/drm/asahi/mmu.rs').is_file()), None))
if kernel is None: raise SystemExit('cannot find Linux source tree')
source = (kernel/'drivers/gpu/drm/asahi/mmu/bind.rs').read_text()
mmu = (kernel/'drivers/gpu/drm/asahi/mmu.rs').read_text()

def block(text, signature):
    if text.count(signature)!=1: raise ValueError('missing or ambiguous '+signature)
    start=text.index(signature); depth=0
    for i in range(text.index('{',start),len(text)):
        if text[i]=='{': depth+=1
        elif text[i]=='}':
            depth-=1
            if not depth: return text[start:i+1]
    raise ValueError('unterminated '+signature)

prepare=block(source,'pub(crate) fn prepare_bind_object(')
coverage=block(mmu,'fn covers(&self, mut offset: u64, size: u64, repeat: bool)')
guard='''        // Reject short backing before GPUVM can remove an overlapping mapping.
        if !vm_bo.data().segments.lock().as_ref().ok_or(EFAULT)?.covers(offset, size, single_page) {
            return Err(EFAULT);
        }
'''
if prepare.count(guard)!=1: raise ValueError('bind coverage guard changed; re-audit')
if sys.argv[1:]==['--without-preflight']:
    prepare=prepare.replace(guard,'',1)
elif sys.argv[1:]:
    raise SystemExit('usage: test_asahi_sg_prepare.py [--without-preflight]')

rust=r'''
#![allow(dead_code,unused_variables,unused_unsafe)]
use std::cell::{Ref,RefCell};
use std::marker::PhantomData;
use std::rc::Rc;
type Result<T=()> = std::result::Result<T,i32>;
const EFAULT:i32=14; const EINVAL:i32=22; const UAT_PGSZ:usize=0x4000;
const UAT_PGMSK:usize=UAT_PGSZ-1; const GFP_KERNEL:()=();
macro_rules! pin_init {($($t:tt)*) => {()}}
struct GpuVaAlloc<T>(PhantomData<T>);
impl<T> GpuVaAlloc<T> {fn new(_:())->Result<Self>{Ok(Self(PhantomData))}}
struct VmInner;
struct VmOpResources {preallocated_gpuvas:[Option<GpuVaAlloc<VmInner>>;3]}
struct SgSegments(Vec<(u64,u64)>);
impl SgSegments {
 fn iter(&self)->std::slice::Iter<'_,(u64,u64)> {self.0.iter()}
COVERAGE
}
struct Lock(RefCell<Option<SgSegments>>);
impl Lock {fn lock(&self)->Ref<'_,Option<SgSegments>> {self.0.borrow()}}
struct Data {segments:Lock}
#[derive(Clone)] struct Bo(Rc<Data>);
impl Bo {fn data(&self)->&Data {&self.0}}
struct GpuVm {bo:Bo}
impl GpuVm {fn obtain(&self,_:&gem::Object,_:())->Result<Bo>{Ok(self.bo.clone())}}
struct Device;
impl Device {unsafe fn as_bound(&self)->&Self {self}}
mod gem {
 use super::*;
 #[derive(Clone)] pub struct Object;
 impl From<&Object> for Object {fn from(v:&Object)->Self {v.clone()}}
 pub fn validate_vm_binding(_:&Object,_:&Vm)->Result {Ok(())}
 pub struct Held;
 impl Held {pub fn into_lease(self){}}
 pub fn held_sg_table(_:&Object,_:&Device)->Result<Held>{Ok(Held)}
}
fn prepare_sgt_segments(_:&Device,_:&gem::Object,_:&Bo,_:&())->Result {Ok(())}
type Prot=();
struct PreparedUserMap {_residency:Option<()>,_sg_lease:(),gem:gem::Object,vm_bo:Bo,
 resources:VmOpResources,prot:Prot,addr:u64,size:u64,offset:u64,single_page:bool,context:()}
struct Vm {dev:Device,gpuvm:GpuVm}
impl Vm {
 fn enter_residency_metadata(&self)->Result<Option<()>> {Ok(None)}
 fn prepare_context_binding(&self,_:u64,_:u64,_:u64)->Result {Ok(())}
PREPARE
}
fn run(segments:&[(u64,u64)],size:u64,repeat:bool,expect:Result) {
 let vm=Vm{dev:Device,gpuvm:GpuVm{bo:Bo(Rc::new(Data{segments:Lock(RefCell::new(
     Some(SgSegments(segments.to_vec()))))}))}};
 let mut existing_mapping=true; let mut commits=0;
 let result=vm.prepare_bind_object(&gem::Object,0x4000,size,0,(),repeat);
 let outcome=match result {Ok(_)=>{existing_mapping=false;commits+=1;Ok(())},Err(e)=>Err(e)};
 assert_eq!(outcome,expect);
 if expect.is_err() {assert!(existing_mapping);assert_eq!(commits,0)}
 else {assert!(!existing_mapping);assert_eq!(commits,1)}
}
fn main(){
 let p=UAT_PGSZ as u64;
 run(&[],p,false,Err(EFAULT));
 run(&[(p,p)],2*p,false,Err(EFAULT));
 run(&[(p,p/2)],2*p,true,Err(EFAULT));
 run(&[(p,p),(3*p,p)],2*p,false,Ok(()));
 run(&[(p,p)],2*p,true,Ok(()));
 println!("actual prepared-bind guard rejects short backing before simulated commit");
}
'''.replace('COVERAGE',coverage).replace('PREPARE',prepare)
env=dict(os.environ)
if workspace and (workspace/'build/rust-toolchain/usr/bin/rustc').is_file():
    tc=workspace/'build/rust-toolchain/usr'
    env['PATH']=str(tc/'bin')+':'+env.get('PATH','')
    env['LD_LIBRARY_PATH']=str(tc/'lib')+':'+env.get('LD_LIBRARY_PATH','')
rustc=shutil.which('rustc',path=env.get('PATH'))
if not rustc: raise SystemExit('rustc not found')
with tempfile.TemporaryDirectory(prefix='asahi-sg-prepare-') as tmp:
    source_path,binary=Path(tmp)/'test.rs',Path(tmp)/'test'
    source_path.write_text(rust)
    subprocess.run([rustc,'--edition=2021',str(source_path),'-o',str(binary)],
                   check=True,env=env,timeout=60)
    subprocess.run([str(binary)],check=True,env=env,timeout=10)

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the actual ADT parser/provenance gate on synthetic normal CPU buffers.

No hardware, VM, module, boot metadata capture or private input. OF/PFN gates
are independently exercised by check_c.py; this harness stubs only that call.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


FIXTURE = r'''
#![allow(dead_code)]
use std::ops::Range;
use std::cell::{Cell, RefCell};
type Result<T=()> = std::result::Result<T,i32>;
const EINVAL:i32=-22;
macro_rules! dev_err {($($x:tt)*)=>{{}};}
mod device {pub struct Device;}
mod hw {pub const HANDOFF_SIZE:u64=0x4000;}
struct GpuAdt {handoff:Range<u64>}
thread_local! {
 static BUFFER:RefCell<Vec<u8>>=const {RefCell::new(Vec::new())};
 static ALLOW:Cell<bool>=const {Cell::new(true)};
 static CALLS:Cell<usize>=const {Cell::new(0)};
}
fn copy()->Result<Vec<u8>> {Ok(BUFFER.with(|v|v.borrow().clone()))}
mod glue {
 pub fn handoff_nc(_: &super::device::Device,start:u64,size:u64)->bool {
  assert_eq!((start,size),(0x200000,0x4000));
  super::CALLS.with(|v|v.set(v.get()+1));
  super::ALLOW.with(|v|v.get())
 }
}
ACTUAL_PARSER
ACTUAL_GATE
fn node(properties:Vec<(&str,Vec<u8>)>,children:Vec<Vec<u8>>)->Vec<u8> {
 let mut out=Vec::new();out.extend_from_slice(&(properties.len() as u32).to_le_bytes());
 out.extend_from_slice(&(children.len() as u32).to_le_bytes());
 for (name,value) in properties {
  assert!(name.len()<32);let mut bytes=[0u8;32];bytes[..name.len()].copy_from_slice(name.as_bytes());
  out.extend_from_slice(&bytes);out.extend_from_slice(&(value.len() as u32).to_le_bytes());
  out.extend_from_slice(&value);while out.len()%4!=0{out.push(0);}
 }
 for child in children {out.extend_from_slice(&child);}out
}
fn bytes(v:u64)->Vec<u8>{v.to_le_bytes().to_vec()}
fn name(label:&str,variant:&str)->Vec<u8> {
 if variant==format!("prefix-{}",label) {return label.as_bytes()[..1].to_vec();}
 if variant==format!("unterminated-{}",label) {return label.as_bytes().to_vec();}
 if variant==format!("padded-{}",label) {return [label.as_bytes(),&[0,0]].concat();}
 [label.as_bytes(),&[0]].concat()
}
fn snapshot(dram_base:u64,dram_size:u64,start:u64,size:u64,compatible:&[u8],missing:&str)->Vec<u8> {
 let mut chosen=vec![("name",name("chosen",missing)),("dram-base",bytes(dram_base)),("dram-size",bytes(dram_size))];
 let mut gpu=vec![("name",name("sgx",missing)),("compatible",compatible.to_vec()),
  ("gfx-handoff-base",bytes(start)),("gfx-handoff-size",bytes(size))];
 chosen.retain(|(n,_)|*n!=missing);gpu.retain(|(n,_)|*n!=missing);
 let chosen=node(chosen,vec![]);let gpu=node(gpu,vec![]);
 let mut gpu_children=vec![gpu.clone()];if missing=="duplicate-sgx" {gpu_children.push(gpu);}
 let armio=node(vec![("name",name("arm-io",missing))],gpu_children);
 let mut root_children=vec![chosen.clone(),armio.clone()];
 if missing=="duplicate-chosen" {root_children.push(chosen);}
 if missing=="duplicate-arm-io" {root_children.push(armio);}
 node(vec![("name",vec![0])],root_children)
}
fn check(data:Vec<u8>,range:Range<u64>,allow:bool,ok:bool,calls:usize) {
 BUFFER.with(|v|*v.borrow_mut()=data);ALLOW.with(|v|v.set(allow));CALLS.with(|v|v.set(0));
 assert_eq!(admit_handoff_nc(&device::Device,&GpuAdt{handoff:range}).is_ok(),ok);
 assert_eq!(CALLS.with(|v|v.get()),calls);
}
fn valid()->Vec<u8>{snapshot(0x100000,0x400000,0x200000,0x4000,b"gpu,t6034\0","")}
fn main() {
 let range=0x200000..0x204000;
 check(valid(),range.clone(),true,true,1);
 check(valid(),range.clone(),false,false,1);
 for missing in ["dram-base","dram-size","gfx-handoff-base","gfx-handoff-size","compatible"] {
  check(snapshot(0x100000,0x400000,0x200000,0x4000,b"gpu,t6034\0",missing),range.clone(),true,false,0);
 }
 for label in ["chosen","arm-io","sgx"] {
  for kind in ["prefix","unterminated","padded","duplicate"] {
   check(snapshot(0x100000,0x400000,0x200000,0x4000,b"gpu,t6034\0",&format!("{}-{}",kind,label)),range.clone(),true,false,0);
  }
 }
 for compat in [b"gpu,t6031\0".as_slice(),b"gpu,t6034".as_slice(),b"gpu,t6034\0extra\0".as_slice()] {
  check(snapshot(0x100000,0x400000,0x200000,0x4000,compat,""),range.clone(),true,false,0);
 }
 for (base,size) in [(0x100000,0),(u64::MAX-0xfff,0x4000),(0x204000,0x100000),(0x100000,0x100000)] {
  check(snapshot(base,size,0x200000,0x4000,b"gpu,t6034\0",""),range.clone(),true,false,0);
 }
 for (start,size) in [(0x200000,0),(0x200000,0x8000),(u64::MAX-0xfff,0x4000),(0x210000,0x4000)] {
  check(snapshot(0x100000,0x400000,start,size,b"gpu,t6034\0",""),range.clone(),true,false,0);
 }
 check(valid(),0x200000..0x208000,true,false,0);
 check(valid(),0x210000..0x214000,true,false,0);
 let data=valid();
 for len in [0,7,8,data.len()/2,data.len()-1] {check(data[..len].to_vec(),range.clone(),true,false,0);}
 let mut corrupt=valid();corrupt[..4].copy_from_slice(&u32::MAX.to_le_bytes());
 check(corrupt,range.clone(),true,false,0);
 // Extreme nesting is refused by the actual parser's depth limit.
 let mut nested=node(vec![("name",b"end\0".to_vec())],vec![]);
 for _ in 0..34 {nested=node(vec![("name",b"nested\0".to_vec())],vec![nested]);}
 check(nested,range,true,false,0);
 println!("PASS actual ADT provenance gate: 39 synthetic cases");
}
'''


def run(kernel, rustc, output):
    source = kernel / 'drivers/gpu/drm/asahi/g15/adt.rs'
    text = source.read_text()
    parser = text[text.index('/// A private copy of the ADT.'):
                  text.index('/// This machine\'s GPU performance states')]
    gate = text[text.index('fn admit_handoff_nc('):]
    # This function currently ends the file. Refuse a future broadened suffix.
    assert gate.count('fn ') == 1 and gate.rstrip().endswith('}')
    code = FIXTURE.replace('ACTUAL_PARSER', parser).replace('ACTUAL_GATE', gate)
    path = output / 'provenance.rs'; path.write_text(code)
    binary = output / 'provenance'
    compiled = subprocess.run([str(rustc), '--edition=2021', '-O', str(path), '-o', str(binary)],
                              capture_output=True, text=True, timeout=60)
    if compiled.returncode:
        raise ValueError('synthetic Rust harness did not compile:\n' + compiled.stderr)
    p = subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)
    mutant = code
    for parent, label in [('0', 'chosen'), ('0', 'arm-io'), ('armio', 'sgx')]:
        exact = 'adt.child_exact(' + parent + ', b"' + label + r'\0")'
        mutant = mutant.replace(exact, 'adt.child(' + parent + ', "' + label + '")')
    if mutant == code:
        raise ValueError('legacy-prefix regression control no longer applies')
    old_path, old_binary = output / 'legacy-prefix.rs', output / 'legacy-prefix'
    old_path.write_text(mutant)
    subprocess.run([str(rustc), '--edition=2021', '-O', str(old_path), '-o', str(old_binary)],
                   check=True, capture_output=True, timeout=60)
    old = subprocess.run([str(old_binary)], capture_output=True, timeout=10)
    if old.returncode == 0:
        raise ValueError('legacy-prefix mutant escaped malformed-name controls')
    return {'actual_Rust_ADT_source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'synthetic_provenance_cases': 39, 'result': p.stdout.strip(),
            'legacy_prefix_matching_mutant_rejected': True,
            'hardware_access': False, 'actual_OF_executed': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel', type=Path, required=True)
    p.add_argument('--rustc', type=Path, required=True)
    a = p.parse_args()
    if not __debug__:
        raise SystemExit('source extraction assertions require nonoptimized Python')
    with tempfile.TemporaryDirectory(prefix='g15c-nc-provenance-') as directory:
        print(json.dumps(run(a.kernel, a.rustc, Path(directory)), indent=2))

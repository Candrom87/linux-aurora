#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Test G15's actual firmware-copy functions against a synthetic nofault API.

The mock can fail after a partial copy or allocation; no device is opened.
This is not a test of physical memory, kernel exception handling, or firmware.
"""
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'drivers/gpu/drm/asahi/g15/identity.rs'


def function(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 0
    for i in range(opening, len(source)):
        depth += (source[i] == '{') - (source[i] == '}')
        if depth == 0:
            return source[start:i+1]
    raise ValueError('unterminated source function')


source = SRC.read_text()
actual = '\n'.join(function(source, sig) for sig in
                   ('fn copy_mapped_snapshot(', 'pub(crate) fn copy_physical('))
test = r'''
#![allow(dead_code,non_upper_case_globals)]
use std::ffi::c_void;
use std::sync::atomic::{AtomicUsize,Ordering};
type Result<T> = std::result::Result<T,i32>;
const ENOMEM:i32=12; const EFAULT:i32=14; const GFP_KERNEL:u32=0;
static UNMAPS:AtomicUsize=AtomicUsize::new(0);
static ALLOC_FAIL:AtomicUsize=AtomicUsize::new(0);
static COPY_FAIL:AtomicUsize=AtomicUsize::new(0);
static COPIES:AtomicUsize=AtomicUsize::new(0);
static BYTES:[u8;8]=[1,2,3,4,5,6,7,8];
#[derive(Debug,PartialEq)] struct KVVec<T>(Vec<T>);
impl KVVec<u8> {
    fn from_elem(value:u8,size:usize,_:u32)->Result<Self> {
        if ALLOC_FAIL.load(Ordering::SeqCst)!=0 {Err(ENOMEM)} else {Ok(Self(vec![value;size]))}
    }
    fn as_mut_ptr(&mut self)->*mut u8 {self.0.as_mut_ptr()}
}
mod bindings {
    use super::*;
    pub const MEMREMAP_WB:u32=1;
    pub unsafe fn memremap(phys:u64,size:usize,_:u32)->*mut c_void {
        if phys==0 || size>BYTES.len() {core::ptr::null_mut()} else {BYTES.as_ptr() as *mut c_void}
    }
    pub unsafe fn memunmap(_: *mut c_void) {UNMAPS.fetch_add(1,Ordering::SeqCst);}
    pub unsafe fn copy_from_kernel_nofault(dst:*mut c_void,src:*const c_void,size:usize)->isize {
        COPIES.fetch_add(1,Ordering::SeqCst);
        let fail=COPY_FAIL.load(Ordering::SeqCst)!=0;
        // SAFETY: tests map BYTES, and both pointers cover size or fewer bytes.
        unsafe {core::ptr::copy_nonoverlapping(src.cast::<u8>(),dst.cast::<u8>(),if fail {size.min(2)} else {size})};
        if fail {-14} else {0}
    }
}
ACTUAL
fn reset() {UNMAPS.store(0,Ordering::SeqCst); ALLOC_FAIL.store(0,Ordering::SeqCst);
            COPY_FAIL.store(0,Ordering::SeqCst); COPIES.store(0,Ordering::SeqCst);}
#[test] fn success_unmaps_and_returns_the_complete_copy() {
    reset(); assert_eq!(copy_physical(1,8),Ok(KVVec(BYTES.to_vec())));
    assert_eq!((UNMAPS.load(Ordering::SeqCst),COPIES.load(Ordering::SeqCst)),(1,1));
}
#[test] fn partial_copy_returns_error_and_unmaps() {
    reset(); COPY_FAIL.store(1,Ordering::SeqCst); assert_eq!(copy_physical(1,8),Err(EFAULT));
    assert_eq!((UNMAPS.load(Ordering::SeqCst),COPIES.load(Ordering::SeqCst)),(1,1));
}
#[test] fn allocation_failure_unmaps_without_copying() {
    reset(); ALLOC_FAIL.store(1,Ordering::SeqCst); assert_eq!(copy_physical(1,8),Err(ENOMEM));
    assert_eq!((UNMAPS.load(Ordering::SeqCst),COPIES.load(Ordering::SeqCst)),(1,0));
}
#[test] fn failed_mapping_neither_unmaps_nor_copies() {
    reset(); assert_eq!(copy_physical(0,8),Err(ENOMEM));
    assert_eq!((UNMAPS.load(Ordering::SeqCst),COPIES.load(Ordering::SeqCst)),(0,0));
}
'''.replace('ACTUAL',actual)

with tempfile.TemporaryDirectory(prefix='g15-nofault-') as directory:
    path = Path(directory)
    (path/'test.rs').write_text(test)
    subprocess.run([os.environ.get('RUSTC','rustc'),'--edition=2021','--test',
                    str(path/'test.rs'),'-o',str(path/'test')],check=True,timeout=30)
    subprocess.run([str(path/'test'),'--test-threads=1'],check=True,timeout=15)
print('Synthetic nofault API only; no physical memory, firmware or GPU access.')

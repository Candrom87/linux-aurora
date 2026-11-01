#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Test actual G15 firmware-object reads with synthetic host memory only."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2] if HERE.parent.name == 'asahi' else HERE.parent / 'linux-aurora'
SOURCE = ROOT / 'drivers/gpu/drm/asahi/g15/object.rs'


def function(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    depth = 0
    for i in range(opening, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ValueError('unterminated function')


source = SOURCE.read_text()
hw = (ROOT / 'drivers/gpu/drm/asahi/g15/hw.rs').read_text()
fw = (ROOT / 'drivers/gpu/drm/asahi/g15/fw/initdata.rs').read_text()
init = (ROOT / 'drivers/gpu/drm/asahi/g15/initdata.rs').read_text()
va_match = re.search(r'const RUNTIME:\s*u64\s*=\s*(0x[0-9a-fA-F]+)', hw)
runtime = re.search(r'pub\(crate\) mod runtime\s*\{(.*?)\n\}', fw, re.S)
ready_match = runtime and re.search(r'const READY:\s*usize\s*=\s*(0x[0-9a-fA-F]+)', runtime.group(1))
if not va_match or not ready_match or '?.r32(runtime::READY)?' not in init:
    raise ValueError('cannot establish runtime readiness read')
if (int(va_match.group(1), 16) + int(ready_match.group(1), 16)) % 4 == 0:
    raise ValueError('runtime readiness no longer exercises the unaligned path')
print('current firmware readiness field follows the unaligned read path', flush=True)
fixture = r'''
#![allow(dead_code)]
use std::ptr::NonNull;
type Result<T> = std::result::Result<T, i32>;
const EINVAL: i32 = 22;
struct FwObject { cpu: Option<NonNull<u8>>, offset: usize, bytes: usize }
impl FwObject {
    PTR
    READ
}
#[no_mangle]
pub unsafe extern "C" fn observe_twice(base: *mut u8) -> u32 {
    let o = FwObject { cpu: NonNull::new(base), offset: 0, bytes: 16 };
    o.r32(1).unwrap() ^ o.r32(1).unwrap()
}
#[cfg(test)] mod tests {
    use super::*;
    #[repr(align(16))] struct Bytes([u8; 16]);
    #[test] fn reads_unaligned_little_endian_and_bounds() {
        let mut bytes = Bytes([0; 16]);
        bytes.0[1..5].copy_from_slice(&[0x12, 0x34, 0x56, 0x78]);
        let o = FwObject { cpu: NonNull::new(bytes.0.as_mut_ptr()), offset: 0, bytes: 16 };
        assert_eq!(o.r32(1), Ok(0x78563412));
        assert_eq!(o.r32(13), Err(EINVAL));
        bytes.0[1..5].copy_from_slice(&[0xef, 0xcd, 0xab, 0x90]);
        assert_eq!(o.r32(1), Ok(0x90abcdef));
        assert_eq!(o.r32(0), Ok(0xabcdef00));
        assert_eq!(o.r32(usize::MAX), Err(EINVAL));
        let no_mapping = FwObject { cpu: None, offset: 0, bytes: 16 };
        assert_eq!(no_mapping.r32(1), Err(EINVAL));
    }
}
'''.replace('PTR', function(source, '    fn ptr(')).replace(
    'READ', function(source, '    pub(crate) fn r32('))

with tempfile.TemporaryDirectory(prefix='g15-unaligned-read-') as tmp:
    rust, binary, ir = (Path(tmp) / name for name in ('fixture.rs', 'fixture', 'fixture.ll'))
    rust.write_text(fixture)
    rustc = os.environ.get('RUSTC', 'rustc')
    subprocess.run([rustc, '--edition=2021', '--test', '-O', str(rust), '-o', str(binary)], check=True)
    subprocess.run([str(binary)], check=True)
    subprocess.run([rustc, '--edition=2021', '--crate-type=lib', '-O', '--emit=llvm-ir',
                    str(rust), '-o', str(ir)], check=True)
    match = re.search(r'define\s+[^\n]*@observe_twice\([^\n]*\).*?^}', ir.read_text(), re.M | re.S)
    if not match:
        raise ValueError('cannot locate generated observe_twice')
    count = len(re.findall(r'asm sideeffect[^\n]*ldr', match.group()))
    if count != 2:
        raise AssertionError(f'expected two side-effecting unaligned word loads, found {count}')
    print('optimized observer retains two unaligned single-instruction loads')

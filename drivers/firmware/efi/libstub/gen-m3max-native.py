#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Bind a reviewed AArch64 EFI application into an isolated candidate kernel.

Only deterministic byte directives are generated; no external path or filename
is embedded in the object. The configured hash must match before compilation.
This verifies container shape/binding, not the application's hardware support.
"""
import argparse
import hashlib
from pathlib import Path
import re
import struct

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--input', type=Path, required=True)
p.add_argument('--sha256', required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
if not re.fullmatch(r'[0-9a-f]{64}', a.sha256):
    p.error('reviewed lowercase SHA256 required')
size = a.input.stat().st_size
if not 256 <= size <= 16 * 1024 * 1024:
    p.error('native application size out of range')
data = a.input.read_bytes()
if hashlib.sha256(data).hexdigest() != a.sha256:
    p.error('native application differs from reviewed SHA256')
pe = struct.unpack_from('<I', data, 0x3c)[0]
if (pe < 64 or pe > len(data) - 96 or data[:2] != b'MZ' or
        data[pe:pe + 4] != b'PE\0\0' or struct.unpack_from('<H', data, pe + 4)[0] != 0xaa64 or
        struct.unpack_from('<H', data, pe + 24)[0] != 0x20b or
        struct.unpack_from('<H', data, pe + 92)[0] != 10):
    p.error('AArch64 PE32+ EFI application required')
lines = ['/* Generated reviewed EFI bytes; do not install separately. */',
         '.section .rodata.m3max_native_bundle,"a",%progbits', '.balign 16',
         '.global m3max_native_bundle_start', 'm3max_native_bundle_start:']
lines += ['.byte ' + ','.join(hex(x) for x in data[i:i+24]) for i in range(0, len(data), 24)]
lines += ['.global m3max_native_bundle_end', 'm3max_native_bundle_end:',
          '.section .note.GNU-stack,"",%progbits', '']
a.output.write_text('\n'.join(lines))

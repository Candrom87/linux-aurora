#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Inspect emitted ARM64 lock/drop bodies in a separately built Asahi object.

This verifies code generation, not physical cache coherency or firmware behavior.
Mutex/time callees are outside the inspected bodies.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('object', type=Path)
    p.add_argument('--receipt', type=Path, required=True)
    args = p.parse_args()
    symbols = subprocess.check_output(['llvm-nm', '--print-size', '--demangle', str(args.object)], text=True)
    names = {
        'acquisition': '<asahi_core::mmu::UatInner>::lock_handoff',
        'drop': 'core::ptr::drop_glue::<asahi_core::mmu::HandoffGuard>',
    }
    result = {}
    for label, name in names.items():
        matches = [line.split(maxsplit=3) for line in symbols.splitlines()
                   if len(line.split(maxsplit=3)) == 4 and line.split(maxsplit=3)[-1] == name]
        assert len(matches) == 1, (label, matches)
        address, length, kind, _ = matches[0]
        assert kind.lower() == 't'
        asm = subprocess.check_output(['llvm-objdump', '-dr', '--start-address=0x'+address,
            '--stop-address='+str(int(address, 16)+int(length, 16)), str(args.object)], text=True)
        assert 'elf64-littleaarch64' in asm
        dsb = len(re.findall(r'\bdsb\s+sy\b', asm))
        assert dsb >= (6 if label == 'acquisition' else 2), (label, dsb)
        # Handoff storage uses ordinary loads/stores. Host mutex callees are deliberately
        # outside this bounded statement and may use ordinary cacheable-RAM atomics.
        assert not re.search(r'\b(?:ldxr|ldaxr|stxr|stlxr|ldxp|ldaxp|stxp|stlxp|cas|casa|casl|casal|swp|swpa|swpl|swpal|ldadd)[bh]?\s', asm)
        assert 'mutex_unlock' in asm
        result[label] = {'dsb_sy': dsb, 'body_size_bytes': int(length, 16), 'exclusive_or_lse_rmw_in_inspected_body': False}
    receipt = {'scope': __doc__, 'object_sha256': hashlib.sha256(args.object.read_bytes()).hexdigest(),
               'llvm_objdump': subprocess.check_output(['llvm-objdump', '--version'], text=True).splitlines()[0],
               'inspected': result}
    args.receipt.write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))

if __name__ == '__main__':
    main()

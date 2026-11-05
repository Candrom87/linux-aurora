#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Inspect real Kbuild AArch64 objects; no loading or hardware access."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

FP = ['g15c_23j220_power', 'ieee_probes', 'arm/pow', 'arm/pow_log_data',
      'arm/exp_data', 'arm/math_err', 'single/powf', 'single/powf_data', 'single/exp2f_data']
INTEGER = ['kernel', 'guard', 'fpcr_read', 'native_select', 'native_import', 'board_source', 'native_caps']


def run(argv):
    p = subprocess.run(list(map(str, argv)), check=True, capture_output=True, text=True, timeout=60)
    return p.stdout


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--objdump', default='llvm-objdump')
    p.add_argument('--nm', default='llvm-nm')
    p.add_argument('--receipt', type=Path)
    p.add_argument('--disabled', action='store_true')
    a = p.parse_args()
    base = a.output / 'drivers/gpu/drm/asahi'
    symbols = run([a.nm, '--defined-only', base / 'asahi.o'])
    apis = ['asahi_g15c_' + n for n in ('decode', 'board_packet', 'source_packet', 'source_bundle')]
    if a.disabled:
        assert all(n not in symbols for n in apis)
        assert not re.search(r'\b(?:g15c_core_|g15c_import_|g15c_port_|g15c_arm_pow|g15c_native_)', symbols)
        result = {'disabled': True, 'no_native_power_symbols_in_asahi': True}
    else:
        assert all(n in symbols for n in apis)
        hashes = {}; frame_max = 0
        all_undefined = set(); all_defined = set()
        for name in FP + INTEGER:
            obj = base / 'g15/power' / (name + '.o')
            cmd = (obj.parent / ('.' + obj.name + '.cmd')).read_text().splitlines()[0]
            assert '-ffp-contract=off' in cmd and '-fno-fast-math' in cmd
            assert not re.search(r'(?<!\S)-flto(?:=\S+)?(?=\s|$)', cmd)
            if name in INTEGER:
                assert '-mgeneral-regs-only' in cmd and '-D_LINUX_FPU_COMPILATION_UNIT' not in cmd
            else:
                assert '-mgeneral-regs-only' not in cmd and '-D_LINUX_FPU_COMPILATION_UNIT' in cmd
            asm = run([a.objdump, '-d', '--no-show-raw-insn', obj])
            instructions = re.findall(r'^\s*[0-9a-f]+:\s+([a-z][a-z0-9.]*)\s*(.*)', asm, re.M)
            if name in INTEGER:
                assert not any(op.startswith('f') or re.search(r'\b[vqdsbh][0-9]+\b', operands)
                               for op, operands in instructions), name
            if name == 'g15c_23j220_power':
                assert not any(op.startswith(('fmadd', 'fmsub', 'fnmadd', 'fnmsub')) for op, _ in instructions)
            # Record the largest observed SP subtraction. The Kbuild compiler
            # separately checks complete frames against its 2048-byte threshold.
            # Nested stack and scheduler/FPSIMD execution need runtime validation.
            for op, operands in instructions:
                if op == 'sub' and operands.startswith('sp, sp, #'):
                    frame_max = max(frame_max, int(operands.split('#')[1].split()[0], 0))
            nm = run([a.nm, obj])
            for line in nm.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] == 'U': all_undefined.add(parts[1])
                elif len(parts) == 3: all_defined.add(parts[2])
            hashes[name + '.o'] = hashlib.sha256(obj.read_bytes()).hexdigest()
        unresolved = all_undefined - all_defined
        assert not any(re.match(r'^(?:powf?|fminf|fma|round|lround|__aeabi_|__.*(?:sf|df)\d)', n) for n in unresolved)
        assert frame_max <= 2048
        result = {'objects': len(FP) + len(INTEGER), 'integer_objects_without_fp_simd': len(INTEGER),
                  'no_lto_across_ownership_boundary': True, 'model_without_contracted_fma': True,
                  'no_unresolved_hosted_math': True, 'largest_sub_sp_bytes': frame_max,
                  'source_object_sha256': hashes}
    if a.receipt: a.receipt.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'source_object_sha256'}, indent=2))


if __name__ == '__main__': main()

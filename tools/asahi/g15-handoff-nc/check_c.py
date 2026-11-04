#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile the exact inactive kernel NC admission body against synthetic OF APIs.

CPU/file only. Simulated OF/resources/PFNs do not establish actual kernel OF,
page-table mappings, early reservation success or physical firmware coherency.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
RELATIVE_SOURCE = Path('drivers/gpu/drm/asahi/g15/handoff_glue.c')


def helper(text):
    match = re.search(r'(?m)^bool asahi_g15_handoff_nc\(struct device \*dev, u64 start, u64 size\)\n\{', text)
    if not match:
        raise ValueError('exact production helper definition is missing')
    start = text.index('{', match.start())
    depth = 0
    for end in range(start, len(text)):
        if text[end] == '{':
            depth += 1
        elif text[end] == '}':
            depth -= 1
            if depth == 0:
                return text[match.start():end + 1] + '\n'
    raise ValueError('production helper definition is incomplete')


def run(argv):
    result = subprocess.run(list(map(str, argv)), capture_output=True, text=True,
                            timeout=30)
    if result.returncode:
        raise RuntimeError('CPU C admission fixture failed:\n' + result.stdout + result.stderr)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--cc', default='cc')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    path = args.kernel / RELATIVE_SOURCE
    raw = path.read_bytes()
    io_source = (args.kernel / 'include/linux/io.h').read_text()
    for name, shift in [('MEMREMAP_WB', 0), ('MEMREMAP_WC', 2)]:
        if not re.search(r'\b' + name + r'\s*=\s*1\s*<<\s*' + str(shift) + r'\s*,', io_source):
            raise ValueError('simulated memory-mode constants differ from kernel source')
    exact = helper(raw.decode()).encode()
    fixture = HERE / 'c_fixture.c'
    results = []
    with tempfile.TemporaryDirectory(prefix='g15c-handoff-nc-c-') as directory:
        tmp = Path(directory)
        (tmp / 'actual_helper.inc').write_bytes(exact)
        for shift in (12, 14):
            binary = tmp / ('fixture-' + str(shift))
            run([args.cc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                 '-Wshadow', '-Wstrict-prototypes', '-Wmissing-prototypes',
                 '-DPAGE_SHIFT=' + str(shift), '-I' + str(tmp), fixture, '-o', binary])
            result = json.loads(run([binary]))
            if result['page_shift'] != shift or not result['all_node_references_released']:
                raise ValueError('CPU fixture receipt mismatch')
            results.append(result)
    if path.read_bytes() != raw:
        raise ValueError('production helper changed during tests; rerun against frozen source')
    receipt = {'scope': 'Actual C helper with simulated OF/resources/PFNs; CPU/file only',
               'source': str(RELATIVE_SOURCE),
               'source_sha256': hashlib.sha256(raw).hexdigest(),
               'exact_helper_sha256': hashlib.sha256(exact).hexdigest(),
               'fixture_sha256': hashlib.sha256(fixture.read_bytes()).hexdigest(),
               'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'strict_compile_diagnostics': 'none',
               'simulated_memory_mode_constants_match_kernel': True, 'results': results,
               'real_OF_executed': False, 'hardware_access': False,
               'kernel_or_module_loaded': False,
               'limits': 'Synthetic OF semantics and RAM classification only; no actual mapping, firmware startup or coherency result'}
    if args.receipt:
        with args.receipt.open('x') as stream:
            json.dump(receipt, stream, indent=2)
            stream.write('\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()

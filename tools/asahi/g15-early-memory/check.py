#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile actual early GPU metadata/runtime source against modeled kernel services.

All addresses and inputs are synthetic. No physical memory, device, firmware,
installed boot artifacts, root operation, or running kernel is accessed.
"""
import argparse
import ctypes as C
import hashlib
import json
import mmap
import re
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
DRAM, USABLE, SOURCE = 0x10000000, 0x10400000, 0x10500000
RANGES = ((DRAM + 0x180000, 0x4000), (DRAM + 0x200000, 0x40000),
          (DRAM + 0x100000, 0x4000), (DRAM + 0x300000, 0x4000))
OWNERS = ('uat-ttbs', 'uat-pagetables', 'uat-handoff', 'uat-pagetables-l2')
NAMES = ('gpu-region', 'gfx-shared-region', 'gfx-handoff', 'gfx-shared-l2-region')


def run(argv, expected=0):
    p = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=60)
    if p.returncode != expected:
        raise ValueError('source fixture process failed:\n' + p.stdout + p.stderr)
    return p


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--kernel', type=Path, required=True)
    p.add_argument('--cc', default='cc')
    p.add_argument('--page-shift', type=int, choices=(12, 14), default=14)
    p.add_argument('--receipt', type=Path)
    p.add_argument('--ubsan', action='store_true')
    a = p.parse_args()
    root = a.kernel.resolve()
    mm = root / 'arch/arm64/mm'
    libfdt = root / 'scripts/dtc/libfdt'
    names = ('fdt', 'fdt_addresses', 'fdt_check', 'fdt_empty_tree', 'fdt_ro', 'fdt_rw', 'fdt_sw', 'fdt_wip', 'fdt_strerror')
    sources = [libfdt / (n + '.c') for n in names]
    sources += [mm / 'asahi-gpu-memory-data.c', HERE / 'fixture.c']
    inputs = sources + [mm / 'asahi-gpu-memory.c', mm / 'asahi-gpu-memory.h',
                        mm / 'init.c', mm / 'Makefile', root / 'arch/arm64/Kconfig.platforms',
                        root / 'arch/arm64/kernel/setup.c',
                        mm / 'mmu.c', mm / 'fixmap.c',
                        root / 'arch/arm64/include/asm/pgtable-hwdef.h',
                        root / 'arch/arm64/include/asm/pgtable-prot.h',
                        root / 'arch/arm64/include/asm/memory.h',
                        root / 'drivers/of/of_reserved_mem.c', root / 'drivers/of/fdt.c',
                        root / 'lib/Makefile', root / 'lib/fdt_check.c',
                        HERE / 'support.h', Path(__file__).resolve()] + sorted(libfdt.glob('*.h'))
    pins = {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest() for f in inputs}
    init = (mm / 'init.c').read_text()
    start = init.index('void __init arm64_memblock_init(void)\n{')
    body = init[start:init.index('\n}\n', start)]
    hook = 'asahi_gpu_early_memory(memory_limit == PHYS_ADDR_MAX, linear_region_size);'
    if body.count(hook) != 1 or not body.rstrip().endswith(hook) or body.index('early_init_fdt_scan_reserved_mem();') > body.index(hook):
        raise ValueError('actual early-memory hook is not after reservations at memblock-init exit')
    setup = (root / 'arch/arm64/kernel/setup.c').read_text()
    if setup.count('arm64_memblock_init();') != 1 or setup.count('paging_init();') != 1 or setup.index('arm64_memblock_init();') > setup.index('paging_init();'):
        raise ValueError('actual ARM64 setup no longer places memory preparation before paging')
    publish = 'asahi_gpu_publish_memory();'
    if (setup.count(publish) != 1 or not setup.index('paging_init();') < setup.index(publish) < setup.index('unflatten_device_tree();')):
        raise ValueError('actual FDT publication is not after paging and before unflattening')
    with tempfile.TemporaryDirectory(prefix='g15c-kernel-early-cpu-') as directory:
        tmp = Path(directory)
        for name in ('linux/types.h', 'linux/init.h', 'linux/limits.h',
                     'linux/string.h', 'linux/unaligned.h', 'linux/efi.h', 'linux/io.h',
                     'linux/kernel.h', 'linux/memblock.h', 'linux/of_fdt.h',
                     'asm/early_ioremap.h', 'asm/memory.h', 'asm/mmu.h'):
            f = tmp / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text('#include "support.h"\n')
        (tmp / 'linux/errno.h').write_text('#include_next <linux/errno.h>\n')
        (tmp / 'linux/libfdt.h').write_text('#include <libfdt.h>\n')
        runtime = (mm / 'asahi-gpu-memory.c').read_text()
        (tmp / 'actual_runtime.c').write_text(runtime)
        mapping = (mm / 'mmu.c').read_text()
        guard_start = mapping.index('bool pgattr_change_is_safe(pteval_t old, pteval_t new)\n{')
        guard = mapping[guard_start:mapping.index('\n}\n', guard_start) + 3]
        (tmp / 'actual_mapping_guard.inc').write_text(guard)
        fdt_source = (root / 'drivers/of/fdt.c').read_text()
        verification = ''
        for name in ('early_init_dt_scan_root', 'early_init_dt_verify'):
            matches = list(re.finditer(r'(?m)^(?:[A-Za-z_]\w*[ \t*]+)+' + re.escape(name) + r'\([^;{}]*\)\n\{', fdt_source))
            if len(matches) != 1: raise ValueError('actual FDT publication verification extraction changed')
            start = matches[0].start(); end = fdt_source.index('\n}\n', start) + 3
            verification += fdt_source[start:end] + '\n'
        (tmp / 'actual_verify.inc').write_text(verification)
        reserved = (root / 'drivers/of/of_reserved_mem.c').read_text()
        scanner = ''
        for name in ('early_init_dt_reserve_memory', '__reserved_mem_reserve_reg',
                     '__reserved_mem_check_root', 'fdt_scan_reserved_mem'):
            matches = list(re.finditer(r'(?m)^(?:[A-Za-z_]\w*[ \t*]+)+' + re.escape(name) + r'\([^;{}]*\)\n\{', reserved))
            if len(matches) != 1: raise ValueError('actual OF scanner extraction changed')
            start = matches[0].start(); end = reserved.index('\n}\n', start) + 3
            body = reserved[start:end]
            scanner += body[:body.index('{')].rstrip() + ';\n' + body + '\n'
        (tmp / 'actual_scan.inc').write_text(scanner)

        def build(path):
            run([a.cc, '-std=gnu11', '-O2', '-fPIC', '-shared', '-Wall', '-Wextra', '-Werror',
                 '-Wshadow', '-Wstrict-prototypes', '-Wmissing-prototypes',
                 *(['-fsanitize=undefined', '-fno-sanitize-recover=all'] if a.ubsan else []),
                 '-DPAGE_SHIFT=' + str(a.page_shift), '-I' + str(tmp), '-I' + str(HERE),
                 '-I' + str(mm), '-I' + str(libfdt), *sources, '-o', path])
        library = tmp / 'early.so'
        build(library)
        outcome = json.loads(run([sys.executable, __file__, '--_worker', library]).stdout)
        mutations = (
            ('if (i != 2 && memblock_add', 'if (memblock_add', 'valid source panicked'),
            ('if (intersects(l->uat[i], md->phys_addr, size))',
             'if (false && intersects(l->uat[i], md->phys_addr, size))', 'unsafe source accepted'),
            ('!reserved_full(source))\n\t\treturn -EINVAL;', 'false)\n\t\treturn -EINVAL;', 'unsafe source accepted'),
            ('mapped = phys_to_virt(staged_tree);',
             'mapped = fixmap_remap_fdt(staged_tree, &size, PAGE_KERNEL_RO);',
             'valid source panicked: ARM64 live-PFN guard'),
        )
        for index, (old, new, error) in enumerate(mutations):
            if runtime.count(old) != 1:
                raise ValueError('mutation anchor changed')
            (tmp / 'actual_runtime.c').write_text(runtime.replace(old, new))
            bad = tmp / ('bad-' + str(index) + '.so')
            build(bad)
            result = run([sys.executable, __file__, '--_worker', bad], expected=1)
            if error not in result.stderr:
                raise ValueError('mutation failed outside intended assertion: ' + str(index) + '\n' + result.stderr)
        outcome['built_mutants_rejected'] = len(mutations)
    for f in inputs:
        if hashlib.sha256(f.read_bytes()).hexdigest() != pins[str(f.relative_to(root))]:
            raise ValueError('source changed during execution')
    receipt = {'scope': __doc__, 'compiler': run([a.cc, '--version']).stdout.splitlines()[0],
               'page_shift': a.page_shift, 'source_sha256': pins,
               'ubsan': a.ubsan,
               'actual_of_scan_fragments_sha256': hashlib.sha256(scanner.encode()).hexdigest(), **outcome,
               'actual_mapping_guard_sha256': hashlib.sha256(guard.encode()).hexdigest(),
               'actual_fdt_verify_and_root_scan_sha256': hashlib.sha256(verification.encode()).hexdigest(),
               'actual_source_hook_after_reservations_before_paging': True,
               'actual_source_publication_after_paging_before_unflatten': True,
               'limits': 'Actual static OF reservation scan, FDT verify/root scan and ARM64 mapping-safety function bodies run with modeled services and synthetic 48-bit PA PTE helpers. CRC uses a hosted scalar implementation. Kernel services, EFI descriptors, allocators, paging/linear mapping and panic are modeled. Actual Make-object compilation is separate. No boot, firmware identity, physical ownership/history, cache coherency or acceleration proof.'}
    if a.receipt:
        with a.receipt.open('x') as stream:
            json.dump(receipt, stream, indent=2)
            stream.write('\n')
    print(json.dumps(receipt, indent=2))


def adt(variant=None):
    def le(x, n=8): return x.to_bytes(n, 'little')
    def node(name, props=(), children=()):
        properties = [('name', name.encode() + b'\0'), *props]
        raw = le(len(properties), 4) + le(len(children), 4)
        for key, data in properties:
            raw += key.encode().ljust(32, b'\0') + le(len(data), 4) + data + bytes((-len(data)) % 4)
        return raw + b''.join(children)
    props = [('compatible', b'gpu,t6034\0' if variant != 'compat' else b'gpu,t6031\0')]
    for i, (base, size) in enumerate(RANGES):
        if variant == 'unaligned' and i == 0: base += 1
        if variant == 'overlap' and i == 1: base = RANGES[0][0]
        if variant == 'size' and i == 2: size *= 2
        if variant == 'outside' and i == 3: base = DRAM + 0x1000000
        props += [(NAMES[i] + '-base', le(base)), (NAMES[i] + '-size', le(size))]
    if variant == 'duplicate-property': props += [(NAMES[0] + '-base', le(RANGES[0][0]))]
    sgx = node('s' if variant == 'prefix' else 'sgx', props)
    armio = node('arm-io', children=[sgx, sgx] if variant == 'duplicate-child' else [sgx])
    chosen = node('chosen', [('dram-base', le(DRAM)), ('dram-size', le(0x1000000, 4 if variant == 'width' else 8))])
    children = [chosen, armio]
    if variant == 'depth':
        deep = node('leaf')
        for _ in range(34): deep = node('branch', children=[deep])
        children.append(deep)
    raw = node('device-tree', [('target-type', b'J516m\0' if variant == 'board' else b'J514m\0')], children)
    if variant == 'truncated': raw = raw[:len(raw) // 2]
    if variant == 'count': raw = b'\xff' * 4 + raw[4:]
    return raw


def exercise(path):
    lib = C.CDLL(str(path))
    V, I, U, Q, S = C.c_void_p, C.c_int, C.c_uint, C.c_uint64, C.c_char_p
    def sig(name, args, result=I):
        fn = getattr(lib, name); fn.argtypes, fn.restype = args, result
    sig('fixture_reset', [V, V, C.c_size_t], None)
    sig('fixture_control', [I, Q], None); sig('fixture_request', [S])
    sig('fixture_run', [I, Q]); sig('fixture_query', [I], Q); sig('fixture_tree', [], V)
    sig('fixture_stage_only', [I, Q]); sig('fixture_publish', [])
    sig('fdt_create_empty_tree', [V, I]); sig('fdt_path_offset', [V, S])
    sig('fdt_add_subnode', [V, I, S]); sig('fdt_setprop_namelen', [V, I, S, I, V, I])
    sig('fdt_set_name', [V, I, S])
    sig('fdt_getprop', [V, I, S, C.POINTER(I)], V)
    sig('fdt_delprop', [V, I, S]); sig('agm_adt_layout', [V, C.c_size_t, V])
    sig('agm_fdt_source', [V, C.c_size_t, V])
    def cells(*xs, width=8): return b''.join(x.to_bytes(width, 'big') for x in xs)
    def setp(dt, path, key, data):
        at = lib.fdt_path_offset(dt, path.encode())
        if at < 0 or lib.fdt_setprop_namelen(dt, at, key.encode(), len(key), data, len(data)): raise ValueError('fixture property edit')
    def getp(dt, path, key):
        length = I(); at = lib.fdt_path_offset(dt, path.encode())
        p = lib.fdt_getprop(dt, at, key.encode(), C.byref(length))
        return C.string_at(p, length.value) if p else None
    def add(dt, parent, name):
        at = lib.fdt_path_offset(dt, parent.encode())
        if at < 0 or lib.fdt_add_subnode(dt, at, name.encode()) < 0: raise ValueError('fixture node edit')
    def tree():
        dt = C.create_string_buffer(65536)
        if lib.fdt_create_empty_tree(dt, len(dt)): raise ValueError('fixture empty tree')
        for name in ('aliases', 'chosen', 'gpu', 'memory', 'reserved-memory'): add(dt, '/', name)
        setp(dt, '/', 'compatible', b'apple,j514m\0apple,t6034\0')
        for path in ('/', '/reserved-memory'):
            setp(dt, path, '#address-cells', cells(2, width=4)); setp(dt, path, '#size-cells', cells(2, width=4))
        setp(dt, '/reserved-memory', 'ranges', b'')
        setp(dt, '/aliases', 'gpu', b'/gpu\0')
        setp(dt, '/gpu', 'compatible', b'apple,agx-t6034\0'); setp(dt, '/gpu', 'status', b'disabled\0')
        setp(dt, '/gpu', 'memory-region-names', b'ttbs\0pagetables\0handoff\0pagetables-l2\0')
        setp(dt, '/gpu', 'memory-region', cells(1, 2, 3, 4, width=4))
        setp(dt, '/memory', 'device_type', b'memory\0'); setp(dt, '/memory', 'reg', cells(USABLE, 0x800000))
        for i, name in enumerate(OWNERS):
            path = '/reserved-memory/' + name + '@' + format(0x10 * (i + 1), 'x')
            add(dt, '/reserved-memory', path.split('/')[-1]); setp(dt, path, 'phandle', cells(i + 1, width=4))
            setp(dt, path, 'reg', cells(0x10 * (i + 1), 0)); setp(dt, path, 'status', b'disabled\0')
        add(dt, '/reserved-memory', 'adt-export')
        setp(dt, '/reserved-memory/adt-export', 'compatible', b'phram\0')
        setp(dt, '/reserved-memory/adt-export', 'label', b'adt\0')
        setp(dt, '/reserved-memory/adt-export', 'reg', cells(SOURCE, 0x4000))
        return dt
    raw_refs = []
    def reset(dt, variant=None):
        raw = C.create_string_buffer(adt(variant)); raw_refs[:] = [raw]
        lib.fixture_reset(dt, raw, len(raw) - 1)
    cases = {'optout': 0, 'success': 0, 'precommit_refusals': 0, 'panic_after_mutation': 0,
             'parser_atomic': 0, 'guarded_prefix_refusals': 0, 'bad_fdt_atomic': 0,
             'mapping_and_phase_regressions': 0}
    dt = tree(); reset(dt)
    if lib.fixture_run(1, 0x1000000) or any(lib.fixture_query(i) for i in range(1, 12)):
        raise ValueError('opt-out performed work')
    cases['optout'] += 1
    for option in (None, b'0', b'1x', b''):
        reset(dt)
        if lib.fixture_request(option) == 0: raise ValueError('bad option accepted')
        lib.fixture_run(1, 0x1000000)
        if lib.fixture_query(1) or lib.fixture_query(3) or lib.fixture_query(9): raise ValueError('bad option touched metadata')
        cases['optout'] += 1
    reset(dt); lib.fixture_request(b'1')
    if lib.fixture_request(b'1') == 0: raise ValueError('duplicate option accepted')
    lib.fixture_run(1, 0x1000000)
    if lib.fixture_query(3): raise ValueError('duplicate option read metadata')
    cases['optout'] += 1
    def accepted(dt, padding=False):
        if padding: setp(dt, '/chosen', 'synthetic-padding', b'x' * 40000)
        reset(dt); before = bytes(dt); lib.fixture_request(b'1')
        if lib.fixture_run(1, 0x1000000):
            raise ValueError('valid source panicked: ARM64 live-PFN guard' if lib.fixture_query(16) else 'valid source panicked')
        if not lib.fixture_query(9): raise ValueError('valid source not published')
        if lib.fixture_query(10): raise ValueError('handoff gained memory')
        if lib.fixture_query(8) or (lib.fixture_query(1), lib.fixture_query(2)) != (4, 3):
            raise ValueError('reserve-before-add order changed')
        if lib.fixture_query(12) != 5: raise ValueError('actual OF inventory count stale')
        if not lib.fixture_query(17): raise ValueError('actual FDT pointer/physical pointer/root cells/CRC update missing')
        if bytes(dt) != before: raise ValueError('original tree modified')
        out = lib.fixture_tree()
        if getp(out, '/gpu', 'status') != b'disabled\0': raise ValueError('GPU enabled')
        if getp(out, '/gpu', 'asahi,g15c-handoff-memory') != b'no-map-normal-nc-v1\0': raise ValueError('policy absent')
        for i, name in enumerate(OWNERS):
            owner = '/reserved-memory/' + name
            if getp(out, owner, 'reg') != cells(*RANGES[i]) or getp(out, owner, 'status') != b'okay\0':
                raise ValueError('owner mismatch')
            if getp(out, owner, 'no-map') != (b'' if i == 2 else None): raise ValueError('owner map policy')
        if getp(out, '/memory', 'reg') != cells(USABLE, 0x800000, *RANGES[0], *RANGES[1], *RANGES[3]):
            raise ValueError('table memory publication mismatch')
        cases['success'] += 1
    accepted(tree()); accepted(tree(), True)
    # This is the actual ARM64 source guard, with synthetic PTE helpers.
    if lib.fixture_query(13) or not lib.fixture_query(14) or not lib.fixture_query(15):
        raise ValueError('actual ARM64 guard no longer rejects live PFN replacement')
    cases['mapping_and_phase_regressions'] += 3
    dt = tree(); reset(dt); lib.fixture_request(b'1')
    if lib.fixture_stage_only(1, 0x1000000) or lib.fixture_query(9) or lib.fixture_query(7):
        raise ValueError('tree published before paging')
    if not lib.fixture_publish():
        raise ValueError('fixture failed to reject a pre-paging linear map access')
    cases['mapping_and_phase_regressions'] += 2
    dt = tree(); reset(dt); lib.fixture_request(b'1')
    if lib.fixture_run(1, 0x1000000): raise ValueError('valid source panicked')
    scans, logs = lib.fixture_query(7), lib.fixture_query(6)
    if lib.fixture_publish() or lib.fixture_query(7) != scans or lib.fixture_query(6) != logs:
        raise ValueError('duplicate publication performed work')
    cases['mapping_and_phase_regressions'] += 1
    def refused(dt, control=None, variant=None, unrestricted=1, linear=0x1000000, free=0):
        reset(dt, variant); before = bytes(dt)
        if control:
            for kind, value in control: lib.fixture_control(kind, value)
        lib.fixture_request(b'1')
        if lib.fixture_run(unrestricted, linear): raise ValueError('precommit refusal panicked')
        if lib.fixture_query(9) or lib.fixture_query(1) or lib.fixture_query(2): raise ValueError('unsafe source accepted: ' + repr((variant, control)))
        if bytes(dt) != before or lib.fixture_query(5) != free: raise ValueError('precommit unwind mismatch')
        cases['precommit_refusals'] += 1
    for typ in (0, 1, 4, 7, 11, 14): refused(tree(), [(3, typ)])
    for control in ([(4, 0)], [(5, 0x1000)], [(6, 1)], [(7, 1)], [(8, 1)], [(9, (1 << 64) - 1)],
                    [(10, 0)], [(11, 0)], [(11, 1 << 63)], [(12, (1 << 64) - 1)], [(13, 1)], [(14, 0)],
                    [(1, 3)], [(1, 4)]): refused(tree(), control)
    refused(tree(), unrestricted=0); refused(tree(), linear=0x100000)
    dt = tree(); setp(dt, '/chosen', 'synthetic-padding', b'x' * 40000)
    for at in (1, 2, 3): refused(dt, [(1, 5), (2, at)], free=1)
    variants = ('board', 'compat', 'prefix', 'duplicate-child', 'duplicate-property', 'width',
                'unaligned', 'overlap', 'size', 'outside', 'depth', 'truncated', 'count')
    for variant in variants:
        refused(tree(), variant=variant)
        raw = C.create_string_buffer(adt(variant)); output = C.create_string_buffer(b'z' * 80, 80)
        before = bytes(output)
        if not lib.agm_adt_layout(raw, len(raw) - 1, output) or bytes(output) != before:
            raise ValueError('parser refusal not atomic')
        cases['parser_atomic'] += 1
    # Every incomplete valid-tree prefix ends exactly at an inaccessible page.
    # This checks actual bounded reads independently of the parser's own test
    # oracle. The pages are anonymous host CPU memory, never device memory.
    libc = C.CDLL(None)
    libc.mprotect.argtypes = [V, C.c_size_t, I]
    libc.mprotect.restype = I
    raw = adt(); page = mmap.PAGESIZE
    extent = ((len(raw) + page - 1) // page) * page
    with mmap.mmap(-1, extent + page, prot=mmap.PROT_READ | mmap.PROT_WRITE) as region:
        address = C.addressof(C.c_char.from_buffer(region))
        if libc.mprotect(address + extent, page, 0): raise ValueError('guard page unavailable')
        try:
            for cut in range(len(raw)):
                pointer = address + extent - cut
                C.memmove(pointer, raw, cut)
                output = C.create_string_buffer(b'z' * 96, 96); before = bytes(output)
                if not lib.agm_adt_layout(pointer, cut, C.addressof(output) + 8) or bytes(output) != before:
                    raise ValueError('incomplete guarded prefix accepted or modified output')
                cases['guarded_prefix_refusals'] += 1
        finally:
            if libc.mprotect(address + extent, page, mmap.PROT_READ | mmap.PROT_WRITE):
                raise ValueError('guard page restoration failed')
    edits = (
        ('/aliases', 'gpu', b'/gpu/\0'), ('/gpu', 'status', b'okay\0'),
        ('/reserved-memory/uat-handoff', 'no-map', b''),
        ('/reserved-memory/uat-ttbs', 'reg', cells(0x99, 0)),
        ('/memory', 'reg', cells(USABLE, 0x800000, *RANGES[0])),
        ('/chosen', 'linux,usable-memory-range', cells(USABLE, 0x800000)),
        ('/reserved-memory', '#size-cells', cells(1, width=4)),
        ('/reserved-memory/adt-export', 'status', b'disabled\0'),
    )
    for path, key, value in edits:
        dt = tree(); setp(dt, path, key, value); refused(dt)
    for field, value in ((0, 0), (4, 65537), (8, 0xfffffff0), (32, 0xfffffff0), (None, 0x1234)):
        dt = tree()
        if field is None:
            field = int.from_bytes(bytes(dt)[8:12], 'big') + lib.fdt_path_offset(dt, b'/gpu')
        dt[field:field + 4] = value.to_bytes(4, 'big')
        output = C.create_string_buffer(b'z' * 32, 32); before = bytes(output)
        if not lib.agm_fdt_source(dt, len(dt), output) or bytes(output) != before:
            raise ValueError('malformed FDT passed full validation or modified output')
        cases['bad_fdt_atomic'] += 1
    for defect in ('phandle', 'competing', 'dynamic', 'adt-label', 'memory'):
        dt = tree()
        if defect == 'memory':
            add(dt, '/', 'memory@0'); setp(dt, '/memory@0', 'device_type', b'memory\0')
        else:
            add(dt, '/reserved-memory', 'other'); path = '/reserved-memory/other'
            if defect == 'phandle': setp(dt, path, 'phandle', cells(1, width=4))
            if defect == 'competing': setp(dt, path, 'reg', cells(DRAM + 0xe00000, 0x4000, *RANGES[2]))
            if defect == 'dynamic': setp(dt, path, 'size', cells(0x4000))
            if defect == 'adt-label': setp(dt, path, 'label', b'adt\0')
        refused(dt)
    dt = tree()
    old = lib.fdt_path_offset(dt, b'/gpu')
    if lib.fdt_set_name(dt, old, b'gpu-hidden'): raise ValueError('fixture rename failed')
    setp(dt, '/gpu-hidden', 'compatible', b'opaque,other\0')
    add(dt, '/', 'gpu')
    for key, value in (
        ('compatible', b'apple,agx-t6034\0'), ('status', b'disabled\0'),
        ('memory-region-names', b'ttbs\0pagetables\0handoff\0pagetables-l2\0'),
        ('memory-region', cells(1, 2, 3, 4, width=4))): setp(dt, '/gpu', key, value)
    old = lib.fdt_path_offset(dt, b'/gpu-hidden')
    if lib.fdt_set_name(dt, old, b'gpu'): raise ValueError('fixture duplicate node failed')
    refused(dt)
    for fault, extent in ((1, 4), (2, 3), (6, 1), (7, 1), (8, 1), (9, 1), (10, 1)):
        for at in range(1, extent + 1):
            dt = tree(); reset(dt); before = bytes(dt)
            lib.fixture_control(1, fault); lib.fixture_control(2, at); lib.fixture_request(b'1')
            if not lib.fixture_run(1, 0x1000000) or not lib.fixture_query(11): raise ValueError('commit failure did not panic')
            if bytes(dt) != before or lib.fixture_query(8): raise ValueError('panic modified source/order')
            if fault == 1 and (lib.fixture_query(1), lib.fixture_query(2)) != (at, 0): raise ValueError('continued after reserve failure')
            if fault == 2 and lib.fixture_query(2) != at: raise ValueError('continued after add failure')
            cases['panic_after_mutation'] += 1
    return {'cases': sum(cases.values()), 'checks': cases, 'warnings': 0,
            'normal_optout_no_access': True, 'reserve_all_before_any_add': True,
            'original_fdt_never_modified': True, 'gpu_disabled_after_preparation': True,
            'precommit_refusal_no_gpu_reservation_or_memory_mutation': True,
            'commit_failures_stop_boot_via_panic': True}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--_worker':
        print(json.dumps(exercise(Path(sys.argv[2]))))
    else:
        main()

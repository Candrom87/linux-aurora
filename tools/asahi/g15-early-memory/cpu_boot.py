#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CPU-only full-kernel regression using entirely invented boot metadata.

Runs an ARM64 kernel under QEMU TCG, with synthetic EFI handoff tables and
retained ADT bytes injected into guest RAM. There is no EFI firmware execution,
Mac hardware, GPU emulation/passthrough, host device, private input or root disk.
This exercises the kernel's real early-memory/paging/OF path, not GPU startup.
"""
import argparse
import ctypes as C
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import time
import zlib

DRAM = 0x40000000
RAM_SIZE = 0x20000000
ADT = 0x5f000000
SYSTAB = ADT + 0x4000
MMAP = SYSTAB + 0x4000
RANGES = ((0x68180000, 0x4000), (0x68200000, 0x40000),
          (0x68100000, 0x4000), (0x68300000, 0x4000))
OWNERS = ('uat-ttbs', 'uat-pagetables', 'uat-handoff', 'uat-pagetables-l2')
NAMES = ('gpu-region', 'gfx-shared-region', 'gfx-handoff', 'gfx-shared-l2-region')
CMDLINE = 'console=ttyAMA0 earlycon=pl011,0x9000000 panic=-1 efi=noruntime nokaslr asahi_t6034_gpu_memory=1'


def run(args, timeout=60):
    return subprocess.run(list(map(str, args)), capture_output=True, timeout=timeout, check=True)


def mapping_bug(text, kernel):
    source = (kernel / 'arch/arm64/mm/mmu.c').read_text()
    function = source.index('static void init_pte(')
    guard = source.index('BUG_ON(!pgattr_change_is_safe(', function)
    end = source.index('));', guard) + 3
    first, last = source.count('\n', 0, guard) + 1, source.count('\n', 0, end) + 1
    lines = re.findall(r'kernel BUG at arch/arm64/mm/mmu\.c:([0-9]+)', text)
    return any(first <= int(line) <= last for line in lines) and all(
        frame in text for frame in ('create_mapping_noalloc+', 'fixmap_remap_fdt+', 'asahi_gpu_early_memory+'))


def canonical_padding(raw):
    """Zero only FDT alignment bytes, preserving every property/name byte."""
    if len(raw) < 40: raise ValueError('short synthetic FDT')
    data = bytearray(raw)
    at = int.from_bytes(data[8:12], 'big')
    end = at + int.from_bytes(data[36:40], 'big')
    if at < 40 or end > len(data): raise ValueError('synthetic FDT structure bounds')
    while at + 4 <= end:
        tag = int.from_bytes(data[at:at + 4], 'big'); at += 4
        if tag == 1:
            zero = data.find(0, at, end)
            if zero < 0: raise ValueError('unterminated synthetic node name')
            start = zero + 1
        elif tag == 3:
            if at + 8 > end: raise ValueError('short synthetic property header')
            size = int.from_bytes(data[at:at + 4], 'big')
            start = at + 8 + size
        elif tag in (2, 4):
            continue
        elif tag == 9 and at == end:
            return bytes(data)
        else:
            raise ValueError('unexpected synthetic FDT token')
        at = (start + 3) & ~3
        if start > end or at > end: raise ValueError('synthetic FDT value bounds')
        data[start:at] = bytes(at - start)
    raise ValueError('missing synthetic FDT end')


def adt_blob():
    def node(name, properties=(), children=()):
        values = [('name', name.encode() + b'\0'), *properties]
        result = struct.pack('<II', len(values), len(children))
        for key, value in values:
            result += key.encode().ljust(32, b'\0') + struct.pack('<I', len(value)) + value + bytes(-len(value) % 4)
        return result + b''.join(children)
    properties = [('compatible', b'gpu,t6034\0')]
    for name, (base, size) in zip(NAMES, RANGES):
        properties.extend(((name + '-base', struct.pack('<Q', base)), (name + '-size', struct.pack('<Q', size))))
    raw = node('device-tree', [('target-type', b'J514m\0')], [
        node('chosen', [('dram-base', struct.pack('<Q', DRAM)), ('dram-size', struct.pack('<Q', 0x40000000))]),
        node('arm-io', children=[node('sgx', properties)])])
    if len(raw) > 0x4000: raise ValueError('synthetic ADT too large')
    return raw.ljust(0x4000, b'\0')


def efi_blob():
    data = bytearray(0xc000)
    struct.pack_into('<QIIII', data, 0, 0x5453595320494249, 0x2001f, 120, 0, 0)
    struct.pack_into('<Q', data, 24, SYSTAB + 0x200)
    struct.pack_into('<I', data, 32, 1)
    struct.pack_into('<Q', data, 104, 1)
    struct.pack_into('<Q', data, 112, SYSTAB + 0x400)
    vendor = 'SYNTHETIC CPU FIXTURE\0'.encode('utf-16le')
    data[0x200:0x200 + len(vendor)] = vendor
    # One unknown config-table GUID, no function pointers. Runtime disabled.
    # Conventional WB RAM ends before every synthetic GPU extent.
    struct.pack_into('<IIQQQQ', data, MMAP - SYSTAB, 7, 0, DRAM, 0, RAM_SIZE // 4096, 8)
    struct.pack_into('<I', data, 16, zlib.crc32(data[:120]))
    return bytes(data)


def prepare_tree(kernel, raw, out):
    lib = kernel / 'scripts/dtc/libfdt'
    library = out / 'synthetic-libfdt.so'
    sources = [lib / (name + '.c') for name in
               ('fdt', 'fdt_addresses', 'fdt_check', 'fdt_empty_tree', 'fdt_ro', 'fdt_rw', 'fdt_sw', 'fdt_wip')]
    shim = out / 'shim'
    here = Path(__file__).resolve().parent
    for name in ('linux/types.h', 'linux/init.h', 'linux/limits.h', 'linux/string.h', 'linux/unaligned.h'):
        path = shim / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('#include "support.h"\n')
    (shim / 'linux/errno.h').write_text('#include_next <linux/errno.h>\n')
    (shim / 'linux/libfdt.h').write_text('#include <libfdt.h>\n')
    mm = kernel / 'arch/arm64/mm'
    run(['gcc', '-O2', '-shared', '-fPIC', '-I' + str(shim), '-I' + str(here),
         '-I' + str(mm), '-I' + str(lib), *sources, mm / 'asahi-gpu-memory-data.c', '-o', library])
    api = C.CDLL(str(library))
    api.fdt_open_into.argtypes = [C.c_void_p, C.c_void_p, C.c_int]
    api.fdt_path_offset.argtypes = [C.c_void_p, C.c_char_p]
    api.fdt_add_subnode.argtypes = [C.c_void_p, C.c_int, C.c_char_p]
    api.fdt_setprop_namelen.argtypes = [C.c_void_p, C.c_int, C.c_char_p, C.c_int, C.c_void_p, C.c_int]
    api.fdt_pack.argtypes = [C.c_void_p]
    api.fdt_delprop.argtypes = [C.c_void_p, C.c_int, C.c_char_p]
    api.agm_fdt_source.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p]
    api.agm_adt_layout.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p]
    api.agm_fdt_plan.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p, C.c_size_t, C.c_void_p]
    tree = C.create_string_buffer(1024 * 1024)
    original = C.create_string_buffer(raw)
    if api.fdt_open_into(original, tree, len(tree)): raise ValueError('cannot open generated QEMU tree')
    def setp(path, key, data):
        node = api.fdt_path_offset(tree, path.encode())
        value = C.create_string_buffer(data)
        if node < 0 or api.fdt_setprop_namelen(tree, node, key.encode(), len(key), value, len(data)):
            raise ValueError('synthetic FDT property failed')
    def add(parent, name):
        node = api.fdt_path_offset(tree, parent.encode())
        if node < 0 or api.fdt_add_subnode(tree, node, name.encode()) < 0:
            raise ValueError('synthetic FDT node failed')
    def cells(*values, width=8): return b''.join(value.to_bytes(width, 'big') for value in values)
    setp('/', 'compatible', b'apple,j514m\0apple,t6034\0linux,dummy-virt\0')
    setp('/', '#address-cells', cells(2, width=4)); setp('/', '#size-cells', cells(2, width=4))
    setp('/chosen', 'bootargs', CMDLINE.encode() + b'\0')
    # Keep old/new comparisons deterministic; no guest entropy is needed.
    for key in (b'rng-seed', b'kaslr-seed'):
        ret = api.fdt_delprop(tree, api.fdt_path_offset(tree, b'/chosen'), key)
        if ret not in (0, -1): raise ValueError('cannot remove synthetic entropy property')
    setp('/memory@40000000', 'reg', cells(DRAM, RAM_SIZE))
    if api.fdt_path_offset(tree, b'/reserved-memory') < 0: add('/', 'reserved-memory')
    for key in ('#address-cells', '#size-cells'): setp('/reserved-memory', key, cells(2, width=4))
    setp('/reserved-memory', 'ranges', b'')
    add('/', 'gpu')
    setp('/gpu', 'compatible', b'apple,agx-t6034\0'); setp('/gpu', 'status', b'disabled\0')
    setp('/gpu', 'memory-region-names', b'ttbs\0pagetables\0handoff\0pagetables-l2\0')
    # Use a high synthetic phandle range, disjoint from QEMU's ordinary nodes.
    setp('/gpu', 'memory-region', cells(0x7001, 0x7002, 0x7003, 0x7004, width=4))
    setp('/aliases', 'gpu', b'/gpu\0')
    for index, name in enumerate(OWNERS):
        path = '/reserved-memory/' + name + '@' + format((index + 1) * 16, 'x')
        add('/reserved-memory', path.rsplit('/', 1)[1])
        setp(path, 'phandle', cells(0x7001 + index, width=4))
        setp(path, 'reg', cells((index + 1) * 16, 0)); setp(path, 'status', b'disabled\0')
    add('/reserved-memory', 'adt-export')
    setp('/reserved-memory/adt-export', 'compatible', b'phram\0')
    setp('/reserved-memory/adt-export', 'label', b'adt\0')
    setp('/reserved-memory/adt-export', 'reg', cells(ADT, 0x4000))
    add('/reserved-memory', 'synthetic-efi')
    setp('/reserved-memory/synthetic-efi', 'reg', cells(SYSTAB, 0xc000))
    for key, value, width in (('linux,uefi-system-table', SYSTAB, 8),
                              ('linux,uefi-mmap-start', MMAP, 8), ('linux,uefi-mmap-size', 40, 4),
                              ('linux,uefi-mmap-desc-size', 40, 4), ('linux,uefi-mmap-desc-ver', 1, 4)):
        setp('/chosen', key, cells(value, width=width))
    if api.fdt_pack(tree): raise ValueError('synthetic tree pack failed')
    size = int.from_bytes(bytes(tree)[4:8], 'big')
    encoded = canonical_padding(bytes(tree)[:size])
    C.memmove(tree, encoded, size)
    source = C.create_string_buffer(16)
    if api.agm_fdt_source(tree, size, source): raise ValueError('actual source parser refuses invented FDT')
    raw = C.create_string_buffer(adt_blob()); layout = C.create_string_buffer(80)
    if api.agm_adt_layout(raw, 0x4000, layout): raise ValueError('actual source parser refuses invented ADT')
    scratch = C.create_string_buffer(2 * 1024 * 1024)
    if api.agm_fdt_plan(tree, size, scratch, len(scratch), layout):
        raise ValueError('actual source planner refuses invented metadata')
    return encoded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--gdb', type=Path, default=Path('/usr/bin/gdb'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--expect-old-pfn-bug', action='store_true')
    args = parser.parse_args()
    if os.geteuid() == 0: raise ValueError('run this CPU fixture as an ordinary user')
    out = args.out.resolve(); out.mkdir(mode=0o700)
    base = [args.qemu.resolve(), '-accel', 'tcg', '-machine', 'virt', '-cpu', 'cortex-a76', '-m', '1024M',
            '-nographic', '-nodefaults', '-serial', 'stdio', '-no-reboot']
    generated = out / 'generated.dtb'
    dump = list(base)
    dump[dump.index('virt')] = 'virt,dumpdtb=' + str(generated)
    run(dump)
    tree = out / 'synthetic.dtb'
    tree.write_bytes(prepare_tree(args.kernel.resolve(), generated.read_bytes(), out))
    adt = out / 'synthetic-adt.bin'; adt.write_bytes(adt_blob())
    efi = out / 'synthetic-efi.bin'; efi.write_bytes(efi_blob())
    socket = out / 'cpu-fixture.sock'
    command = base + ['-kernel', args.image.resolve(), '-dtb', tree, '-append', CMDLINE,
        '-device', 'loader,file=' + str(adt) + ',addr=' + hex(ADT) + ',force-raw=on',
        '-device', 'loader,file=' + str(efi) + ',addr=' + hex(SYSTAB) + ',force-raw=on',
        '-S', '-gdb', 'unix:' + str(socket) + ',server=on,wait=off']
    # QEMU rewrites /memory/reg in a supplied DTB to describe all its RAM.
    # Restore the validated, deliberately excluded metadata while its own
    # direct-boot trampoline is paused, before any kernel instruction runs.
    # Decode/assert the trampoline's first LDR X0 literal rather than guessing
    # a kernel load address. Only invented bytes in this owned guest are written.
    restore = out / 'restore-invented-tree.gdb'
    restore.write_text('set pagination off\nset auto-load off\n' +
        'target remote ' + str(socket) + '\npython\n' +
        'import gdb, struct\n' +
        'guest = gdb.selected_inferior()\n' +
        'pc = int(gdb.parse_and_eval("$pc"))\n' +
        'assert pc == ' + str(DRAM) + ', "unexpected emulated trampoline address"\n' +
        'word, = struct.unpack("<I", bytes(guest.read_memory(pc, 4)))\n' +
        'assert word & 0xff00001f == 0x58000000, "expected trampoline LDR X0 literal"\n' +
        'imm = (word >> 5) & 0x7ffff\n' +
        'if imm & 0x40000: imm -= 0x80000\n' +
        'address, = struct.unpack("<Q", bytes(guest.read_memory(pc + imm * 4, 8)))\n' +
        'data = open(' + repr(str(tree)) + ', "rb").read()\n' +
        'assert ' + str(DRAM) + ' <= address < address + len(data) <= ' + str(DRAM + 0x40000000) + '\n' +
        'guest.write_memory(address, data)\n' +
        'assert bytes(guest.read_memory(address, len(data))) == data\n' +
        'print("Invented FDT restored and read back before kernel execution.")\n' +
        'end\ndetach\n')
    logfile = out / 'cpu-only-kernel.log'
    stopped_at_expected_bug = timed_out = False
    with logfile.open('wb') as stream:
        process = subprocess.Popen(list(map(str, command)), stdout=stream, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 5
            while not socket.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise ValueError('owned QEMU debugger socket did not become ready')
                time.sleep(0.02)
            debug = run([args.gdb.resolve(), '-nx', '-q', '-batch', '-x', restore], timeout=15)
            (out / 'cpu-only-debugger.log').write_bytes(debug.stdout + debug.stderr)
            if b'Invented FDT restored and read back before kernel execution.' not in debug.stdout:
                raise ValueError('owned guest metadata readback was not confirmed')
            deadline = time.monotonic() + 45
            while process.poll() is None:
                if args.expect_old_pfn_bug and mapping_bug(logfile.read_text(errors='replace'), args.kernel.resolve()):
                    stopped_at_expected_bug = True
                    break
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                process.kill(); process.wait()
    log = logfile.read_bytes()
    text = log.decode(errors='replace')
    result = {'scope': __doc__, 'all_boot_metadata_invented': True, 'no_gpu_device_or_passthrough': True,
              'invented_fdt_restored_before_any_kernel_instruction': True,
              'synthetic_efi_recognized': 'SYNTHETIC CPU FIXTURE' in text,
              'memory_preparation_accepted': 'T6034 GPU memory prepared; GPU remains disabled' in text,
              'old_mapping_guard_bug_seen': mapping_bug(text, args.kernel.resolve()),
              'owned_guest_stopped_after_expected_early_bug': stopped_at_expected_bug,
              'guest_timed_out': timed_out,
              'reaches_expected_no_root_disk_panic': 'VFS: Unable to mount root fs' in text,
              'physical_gpu_or_mac_efi_stub_test': False}
    (out / 'receipt.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
    expected = (result['old_mapping_guard_bug_seen'] and not result['memory_preparation_accepted']
                if args.expect_old_pfn_bug else
                result['memory_preparation_accepted'] and result['reaches_expected_no_root_disk_panic'])
    if timed_out or not result['synthetic_efi_recognized'] or not expected:
        raise ValueError('CPU-only regression did not reach the required outcome; inspect its synthetic log')


if __name__ == '__main__': main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Actual integer entry + numerical sources with modeled kernel ownership.

Runs only ordinary AArch64 userspace CPU arithmetic on invented data. Does not
load a module or access a GPU, device, firmware, boot metadata or private input.
"""
import argparse
import ctypes as C
import hashlib
import json
from pathlib import Path
import platform
import random
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
POWER = ROOT / 'drivers/gpu/drm/asahi/g15/power'


class Coefficients(C.Structure):
    _fields_ = [('main_bits', C.c_uint32 * 4), ('cs_bits', C.c_uint32 * 2),
                ('main_present', C.c_uint32), ('cs_present', C.c_uint32)]


class Packed(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('w124', 'w128', 'w12c', 'w134', 'present')]


class MainRow(C.Structure):
    _fields_ = [('hz', C.c_uint32), ('mv', C.c_uint32 * 4)]


class CsRow(C.Structure):
    _fields_ = [('hz', C.c_uint32), ('mv', C.c_uint32 * 2)]


class PowerInput(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('contract', 'main_states', 'cs_states', 'lanes', 'cs_channels')] + [
        ('coefficients', Coefficients), ('enabled_uscs', C.c_uint32 * 4),
        ('uscs_present', C.c_uint32), ('cs_activity', C.c_uint32 * 2),
        ('activity_present', C.c_uint32), ('main', MainRow * 16), ('cs', CsRow * 16)]


class PowerOutput(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('main_states', 'cs_states', 'lanes', 'cs_channels')] + [
        ('cs_words', (C.c_uint32 * 2) * 16), ('lane_words', (C.c_uint32 * 4) * 16),
        ('row_sum_u32', C.c_uint32 * 16), ('relative_percent_u32', C.c_uint32 * 16),
        ('reference_word', C.c_uint32)]


class Input(C.Structure):
    _fields_ = [('source', PowerInput), ('map_banks', (C.c_uint32 * 8) * 2),
                ('chip_id', C.c_uint32), ('masks_msb', (C.c_uint32 * 4) * 3),
                ('masks_present', C.c_uint32)]


class Packet(C.Structure):
    _fields_ = [('length', C.c_uint32), ('bytes', C.c_uint8 * 256)]


class Property(C.Structure):
    _fields_ = [('bytes', C.POINTER(C.c_uint8)), ('length', C.c_size_t)]


class Source(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('contract', 'chip_id', 'gpu_num_perf_states', 'table_count')] + [
        ('main', Property), ('sram', Property), ('cs', Property), ('afr', Property),
        ('identity', C.c_uint32 * 6), ('coefficients', Packed),
        ('map_banks', (C.c_uint32 * 8) * 2), ('cs_activity_mask', C.c_uint32),
        ('masks_msb', (C.c_uint32 * 4) * 3), ('masks_present', C.c_uint32)]


def run(argv):
    p = subprocess.run(list(map(str, argv)), capture_output=True, text=True, timeout=120)
    if p.returncode or 'warning:' in p.stderr:
        raise RuntimeError('source fixture command failed:\n' + p.stdout + p.stderr)
    return p.stdout


def build(tmp, cc, mutant=None, ubsan=False):
    fake = tmp / 'fake'; fake.mkdir()
    for name in ('linux/errno.h', 'linux/fpu.h', 'linux/slab.h', 'linux/string.h',
                 'asm/simd.h', 'asm/sysreg.h'):
        p = fake / name; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('#include "support.h"\n')
    (fake / 'linux/errno.h').write_text('#include_next <linux/errno.h>\n')
    common = [cc, '-O2', '-fPIC', '-Wall', '-Wextra', '-Werror', '-fno-fast-math',
              '-ffp-contract=off', '-fno-math-errno', '-fno-builtin', '-I' + str(POWER),
              '-I' + str(POWER / 'include'), '-I' + str(HERE)]
    if ubsan:
        common += ['-fsanitize=undefined', '-fno-sanitize-recover=all']
    fp = ['g15c_23j220_power', 'ieee_probes', 'arm/pow', 'arm/pow_log_data',
          'arm/exp_data', 'arm/math_err', 'single/powf', 'single/powf_data', 'single/exp2f_data']
    integer = ['kernel', 'guard', 'fpcr_read', 'native_select', 'native_import', 'board_source']
    objects = []
    for name in fp + integer + ['fixture']:
        source = HERE / 'fixture.c' if name == 'fixture' else POWER / (name + '.c')
        if name == 'kernel' and mutant:
            original = source.read_text()
            changes = {
                'restore_fpcr': ('write_sysreg_s(fpcr, SYS_FPCR);', '(void)fpcr;'),
                'restore_fpsr': ('write_sysreg_s(fpsr, SYS_FPSR);', '(void)fpsr;'),
                'canonical': ('write_sysreg_s(0, SYS_FPCR);', '/* omit canonical control */'),
                'nested': ('test_thread_flag(TIF_KERNEL_FPSTATE)', 'false'),
                'selector': ('in->source.main[work->selection.primary[state]]', 'in->source.main[state]'),
            }
            old, new = changes[mutant]
            assert old in original
            source = tmp / 'kernel_mutant.c'; source.write_text(original.replace(old, new))
        flags = common[:]
        if name not in fp:
            flags += ['-mgeneral-regs-only']
        if name in ('kernel', 'fixture'):
            flags += ['-I' + str(fake)]
        if name == 'g15c_23j220_power':
            flags += ['-Dg15c_23j220_' + n + '=g15c_core_' + n
                      for n in ('decode', 'cs_word', 'main_word', 'produce')]
        if name == 'ieee_probes':
            flags += ['-Dg15c_port_ieee_probes=g15c_actual_ieee_probes']
        if name == 'board_source':
            flags += ['-Dg15c_23j220_decode=asahi_g15c_decode']
        if name.startswith('arm/'):
            flags += ['-DUSE_GLIBC_ABI=0', '-DWANT_ERRNO=0', '-DWANT_ERRNO_UFLOW=0',
                      '-DWANT_ROUNDING=1', '-DWANT_EXPERIMENTAL_MATH=0', '-DHAVE_FAST_FMA=1',
                      '-I' + str(POWER / 'arm/include')]
        if name.startswith('single/'):
            flags += ['-include', str(POWER / 'single/namespace.h')]
        obj = tmp / (name.replace('/', '_') + '.o')
        run(flags + ['-c', source, '-o', obj]); objects.append(obj)
    lib = tmp / 'actual.so'
    run([cc, '-shared', '-Wl,-z,defs', *objects, '-o', lib] +
        (['-fsanitize=undefined'] if ubsan else []))
    # Same original source-order formula against independent hosted libm.
    reference = tmp / 'reference.so'
    run([cc, '-O2', '-fPIC', '-shared', '-ffp-contract=off', '-fno-fast-math',
         POWER / 'g15c_23j220_power.c', '-lm', '-o', reference])
    return lib, reference


def bits(v):
    return struct.unpack('<I', struct.pack('<f', v))[0]


def synthetic(trial):
    rng = random.Random(0x1120000 + trial)
    x = Input(); x.chip_id = 0x6034; x.masks_present = 7
    s = x.source
    s.contract, s.main_states, s.cs_states, s.lanes = 0x23c22001, 2 + trial % 13, 2 + trial % 15, 4
    s.cs_channels = 1 + trial % 2
    s.coefficients.main_present = s.uscs_present = 15
    s.coefficients.cs_present = s.activity_present = (1 << s.cs_channels) - 1
    for lane in range(4):
        s.coefficients.main_bits[lane] = bits((2, 20, 200, 2000)[lane])
        s.enabled_uscs[lane] = 3 + lane
    for ch in range(s.cs_channels):
        s.coefficients.cs_bits[ch] = bits(12 + ch * 108)
        s.cs_activity[ch] = 5 + ch * 8
    for row in range(s.main_states):
        s.main[row].hz = (24 + 53 * row) * 1_000_000 + trial % 1000
        s.main[row].mv[:] = [500 + 37 * row + lane * 3 for lane in range(4)]
    for row in range(s.cs_states):
        s.cs[row].hz = (24 + 31 * row) * 1_000_000
        s.cs[row].mv[:s.cs_channels] = [625 + row * 13 + ch for ch in range(s.cs_channels)]
    for bank in x.map_banks:
        indices = [rng.randrange(16)] + [0] * 15
        for slot in rng.sample(range(1, 16), 1 + trial % 15):
            indices[slot] = rng.randrange(1, s.main_states)
        bank[:] = [indices[i] | indices[i + 1] << 16 | (rng.getrandbits(32) & 0xfff0fff0)
                   for i in range(0, 16, 2)]
    for mask in x.masks_msb:
        mask[:] = [rng.getrandbits(32) for _ in range(4)]
    return x


def selected(x):
    p, s = [0], [0]
    for i in range(1, 16):
        a, b = [(x.map_banks[n][i // 2] >> (16 * (i % 2))) & 15 for n in (0, 1)]
        if a > x.source.main_states:
            break
        if a: p.append(a)
        if b: s.append(b)
    return p, s


def expected(x, ref):
    p, s = selected(x)
    assert len(p) == len(s) and len(p) >= 2
    power_in = PowerInput.from_buffer_copy(x.source)
    power_in.main_states = len(p)
    for i, raw in enumerate(p): power_in.main[i] = x.source.main[raw]
    out = PowerOutput()
    assert ref.g15c_23j220_produce(C.byref(power_in), C.byref(out)) == 0
    cells = [0x23c22001, x.chip_id, out.reference_word, len(p)]
    cells += [v for mask in x.masks_msb for v in mask]
    cells += p + s + list(out.relative_percent_u32)[:len(p)]
    return struct.pack('>' + 'I' * len(cells), *cells)


def source_case(trial, ref):
    # Raw buffers and the independent expected imported model use deliberately
    # different table frequencies, trailing bytes, absent/present SRAM, uV
    # truncation and wide auxiliary Hz. No retained ADT is read.
    prepared = synthetic(trial * 2)
    s = prepared.source; s.cs_channels = s.activity_present = s.coefficients.cs_present = 1
    count, cs_count = s.main_states, s.cs_states
    src = Source(contract=0x23c22001, chip_id=0x6034, gpu_num_perf_states=count - 1,
                 table_count=4, masks_present=7)
    src.identity[:] = [0x07040000, 0x0001040a, 0, 0x12345678, 0x56, 0]
    src.coefficients = Packed(0x12345678, 0x23456789, 0x3456789a, 0x456789ab, 15)
    assert ref.g15c_23j220_decode(C.byref(src.coefficients), C.byref(s.coefficients)) == 0
    mask = src.identity[3] | src.identity[4] << 32
    s.enabled_uscs[:] = [(mask >> (lane * 10) & 1023).bit_count() for lane in range(4)]
    src.cs_activity_mask = 0x12345
    s.cs_activity[0] = src.cs_activity_mask.bit_count()
    src.map_banks = prepared.map_banks; src.masks_msb = prepared.masks_msb
    main = b''.join(struct.pack('<II', s.main[row].hz + lane * 17, s.main[row].mv[lane])
                    for lane in range(4) for row in range(count))
    sram = b''
    if trial % 2:
        sram = b''.join(struct.pack('<II', s.main[row].hz + lane * 19 + 1001, 900 + row)
                        for lane in range(4) for row in range(count))
    for row in range(count): s.main[row].hz += 3 * (19 if sram else 17) + (1001 if sram else 0)
    def auxiliary(n, frequency_offset):
        return struct.pack('<QQ', 1, n) + b''.join(
            struct.pack('<QQ', s.cs[row].mv[0] * 1000 + 999,
                        s.cs[row].hz + frequency_offset + (1 << 32)) for row in range(n)) + struct.pack('<Q', 789000)
    cs = auxiliary(cs_count, 0)
    afr = auxiliary(min(cs_count, 8), 123456)
    keep = []
    for name, data in [('main', main + b'tail'), ('sram', sram), ('cs', cs), ('afr', afr)]:
        if data:
            buf = (C.c_uint8 * len(data)).from_buffer_copy(data); keep.append(buf)
            setattr(src, name, Property(buf, len(data)))
    return src, prepared, keep


def exercise(lib_path, ref_path, reader):
    lib, ref = C.CDLL(str(lib_path)), C.CDLL(str(ref_path))
    lib.fixture_call.argtypes = [C.c_uint, C.c_uint64, C.c_uint64, C.c_uint,
                                C.c_void_p, C.c_uint32, C.c_void_p]
    ref.g15c_23j220_produce.argtypes = [C.POINTER(PowerInput), C.POINTER(PowerOutput)]
    ref.g15c_23j220_decode.argtypes = [C.POINTER(Packed), C.POINTER(Coefficients)]
    checks = {'packets': 0, 'source_packets': 0, 'coefficient_decodes': 0, 'atomic_refusals': 0}
    packets = bytearray()
    controls = (0, 1 << 22, 2 << 22, 3 << 22, 1 << 24, 1 << 25, (1 << 24) | (3 << 22))
    def call(x, flags=0, control=0, status=0, decode=False):
        out = Coefficients() if decode == 1 else Packet()
        C.memset(C.byref(out), 0xa5, C.sizeof(out)); before = bytes(out)
        result = lib.fixture_call(flags, control, 0x0800009f, decode, C.byref(x), 0x6034, C.byref(out))
        assert result == status, (result, status)
        if status: assert bytes(out) == before
        return out
    for trial in range(208):
        x = synthetic(trial)
        target = expected(x, ref)
        for control in controls:
            out = call(x, control=control)
            assert bytes(out.bytes)[:out.length] == target, 'packet formula/selection mismatch'
            assert bytes(out.bytes)[out.length:] == bytes(256 - out.length)
            packets += struct.pack('<I', out.length) + bytes(out.bytes)[:out.length]
            checks['packets'] += 1
    rng = random.Random(0x23c220)
    for trial in range(128):
        x = Packed(*(rng.getrandbits(32) for _ in range(4)), 15)
        want = Coefficients()
        assert ref.g15c_23j220_decode(C.byref(x), C.byref(want)) == 0
        for control in controls:
            assert bytes(call(x, control=control, decode=True)) == bytes(want)
            checks['coefficient_decodes'] += 1
    for trial in range(104):
        src, prepared, keep = source_case(trial, ref)
        target = expected(prepared, ref)
        for control in controls:
            out = call(src, control=control, decode=2)
            assert bytes(out.bytes)[:out.length] == target, 'source formula/selection mismatch'
            assert bytes(out.bytes)[out.length:] == bytes(256 - out.length)
            packets += struct.pack('<I', out.length) + bytes(out.bytes)[:out.length]
            checks['source_packets'] += 1
    for decode in (False, True):
        good = Packed(0, 0, 0, 0, 15) if decode else synthetic(6)
        for flags, status in [(1, -11), (2, -11), (4, -11), (8, -11),
                              (16, -95), (32, -95), (64, -11), (256, -3)] + ([] if decode else [(128, -12)]):
            for control in controls:
                call(good, flags=flags, control=control, status=status, decode=decode)
                checks['atomic_refusals'] += 1
    bad = []
    for field, value in [('contract', 0), ('main_states', 0), ('main_states', 15),
                         ('lanes', 2), ('cs_states', 0), ('cs_states', 17),
                         ('cs_channels', 0), ('uscs_present', 0), ('activity_present', 0)]:
        x = synthetic(6); setattr(x.source, field, value); bad.append(x)
    x = synthetic(6); x.map_banks[1][0] = 15 << 16; bad.append(x)
    x = synthetic(6); x.map_banks[0][0] = x.source.main_states << 16; bad.append(x)
    for coeff in (0, 0x7f800000, 0x7fc00000, 0xbf800000):
        x = synthetic(6); x.source.coefficients.main_bits[0] = coeff; bad.append(x)
    # Refuse after earlier rows have already been calculated.
    x = synthetic(6); x.source.cs[x.source.cs_states - 1].mv[0] = 1501; bad.append(x)
    x = synthetic(6); p, _ = selected(x); x.source.main[p[-1]].mv[3] = 1501; bad.append(x)
    for x in bad:
        for control in controls:
            out = Packet(); C.memset(C.byref(out), 0xa5, C.sizeof(out)); before = bytes(out)
            assert lib.fixture_call(0, control, 0x0800009f, 0, C.byref(x), 0x6034, C.byref(out)) != 0
            assert bytes(out) == before
            checks['atomic_refusals'] += 1
    for flags, status in [(1, -11), (8, -11), (16, -95), (64, -11), (128, -12), (256, -3), (512, -12)]:
        src, _, keep = source_case(6, ref)
        for control in controls:
            call(src, flags=flags, control=control, status=status, decode=2)
            checks['atomic_refusals'] += 1
    for identity_index, value in [(0, 0), (1, 0), (4, 256), (5, 1)]:
        src, _, keep = source_case(7, ref)
        src.identity[identity_index] = value
        for control in controls:
            call(src, control=control, status=-1, decode=2)
            checks['atomic_refusals'] += 1
    for field in ('main', 'sram', 'cs', 'afr'):
        src, _, keep = source_case(7, ref)
        getattr(src, field).length = 1
        for control in controls:
            call(src, control=control, status=-1, decode=2)
            checks['atomic_refusals'] += 1
    accepted = subprocess.run([str(reader)], input=bytes(packets), capture_output=True, timeout=60)
    assert accepted.returncode == 0, accepted.stderr.decode()
    checks['actual_rust_reader_packets'] = checks['packets'] + checks['source_packets']
    return checks


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cc', default='cc'); p.add_argument('--receipt', type=Path)
    p.add_argument('--mutants', action='store_true'); p.add_argument('--ubsan', action='store_true')
    p.add_argument('--rustc', default='rustc')
    p.add_argument('--worker', nargs=3, type=Path, help=argparse.SUPPRESS)
    a = p.parse_args()
    if a.worker:
        print(json.dumps(exercise(*a.worker))); return
    if platform.machine() != 'aarch64':
        p.error('this FPCR/FPSR CPU fixture requires AArch64 userspace')
    # Verify every exact vendored source before compiling it.
    provenance = json.loads((POWER / 'provenance.json').read_text())
    for name, pin in provenance['exact_files'].items():
        assert hashlib.sha256((POWER / name).read_bytes()).hexdigest() == pin['sha256']
    with tempfile.TemporaryDirectory(prefix='g15c-native-kernel-cpu-') as d:
        tmp = Path(d); baseline = tmp / 'base'; baseline.mkdir()
        lib, ref = build(baseline, a.cc, ubsan=a.ubsan)
        board = ROOT / 'drivers/gpu/drm/asahi/g15/board'
        code = (HERE / 'reader.rs').read_text()
        for name in ('words', 'record'):
            code = code.replace('"' + name.upper() + '"', json.dumps(str(board / (name + '.rs'))))
        source = tmp / 'reader.rs'; source.write_text(code)
        reader = tmp / 'reader'
        run([a.rustc, '--edition=2021', '-O', source, '-o', reader])
        import sys
        result = json.loads(run([sys.executable, __file__, '--worker', lib, ref, reader]))
        rejected = []
        if a.mutants:
            for mutant in ('restore_fpcr', 'restore_fpsr', 'canonical', 'nested', 'selector'):
                sub = tmp / mutant; sub.mkdir()
                library, oracle = build(sub, a.cc, mutant=mutant, ubsan=a.ubsan)
                attempt = subprocess.run([sys.executable, __file__, '--worker', str(library), str(oracle), str(reader)],
                                         capture_output=True, timeout=120)
                expected_failure = {
                    'restore_fpcr': b'AssertionError: (-4095, 0)',
                    'restore_fpsr': b'AssertionError: (-4095, 0)',
                    'canonical': b'AssertionError: (-3, 0)',
                    'nested': b'AssertionError: (-4095, -11)',
                    'selector': b'AssertionError: packet formula/selection mismatch',
                }[mutant]
                assert attempt.returncode == 1 and expected_failure in attempt.stderr, 'unexpected mutant outcome: ' + mutant
                rejected.append(mutant)
    result.update({'compiler': run([a.cc, '--version']).splitlines()[0],
                   'mutants_rejected': rejected,
                   'scope': 'ordinary CPU FPCR/FPSR; kernel services modeled; invented inputs only',
                   'source_sha256': {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
                                     for f in sorted(POWER.rglob('*')) if f.is_file()}})
    inputs = [HERE / n for n in ('support.h', 'fixture.c', 'check.py', 'reader.rs', 'check_objects.py')]
    inputs += [ROOT / 'drivers/gpu/drm/asahi' / n for n in ('Makefile', 'Kconfig', 'g15/board/record.rs', 'g15/board/words.rs')]
    result['source_sha256'].update({str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in inputs})
    if a.receipt: a.receipt.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'source_sha256'}, indent=2))


if __name__ == '__main__':
    main()

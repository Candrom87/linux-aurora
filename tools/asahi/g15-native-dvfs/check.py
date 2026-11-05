#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Actual C raw source/FPU/packet/bundle -> actual Rust complete record.

Invented CPU buffers only. No device, retained private input, module load or
boot operation. Kernel services are the E112 userspace models.
"""
import argparse
import ctypes as C
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
spec = importlib.util.spec_from_file_location('native_power_cpu', HERE.parent / 'g15-native-power/check.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)


class Main(C.Structure):
    _fields_ = [('count', C.c_uint32), ('tables', C.c_uint32), ('hz', C.c_uint32 * 16),
                ('mv', (C.c_uint32 * 8) * 16), ('sram_mv', (C.c_uint32 * 8) * 16)]


class Aux(C.Structure):
    _fields_ = [('count', C.c_uint32), ('channels', C.c_uint32), ('hz', C.c_uint32 * 16),
                ('mv', (C.c_uint32 * 2) * 16), ('sram_mv', (C.c_uint32 * 2) * 16)]


class Tables(C.Structure):
    _fields_ = [('main', Main), ('cs', Aux), ('afr', Aux), ('caps', C.c_uint32 * 3)]


class Bundle(C.Structure):
    _fields_ = [('packet', m.Packet), ('tables', Tables)]


def oracle(src, selected_count, revision):
    out = Tables(); main = out.main
    main.count, main.tables = src.gpu_num_perf_states + 1, src.table_count
    for field, raw in [('mv', src.main), ('sram_mv', src.sram)]:
        dest = getattr(main, field)
        if not raw.bytes:
            for row in range(main.count):
                for lane in range(main.tables):
                    dest[row][lane] = 789 if main.mv[row][lane] < 789 else main.mv[row][0]
            continue
        data = C.string_at(raw.bytes, raw.length)
        for lane in range(main.tables):
            for row in range(main.count):
                main.hz[row], dest[row][lane] = struct.unpack_from('<II', data, 8 * (lane * main.count + row))
        for row in range(main.count):
            for lane in range(1, 8):
                if not dest[row][lane]: dest[row][lane] = dest[row][0]
    for name in ('cs', 'afr'):
        raw, dest = getattr(src, name), getattr(out, name)
        data = C.string_at(raw.bytes, raw.length)
        dest.channels, dest.count = struct.unpack_from('<QQ', data)
        for ch in range(dest.channels):
            floor = (struct.unpack_from('<Q', data, 16 + dest.channels * dest.count * 16 + ch * 8)[0] // 1000) & 0xffffffff
            for row in range(dest.count):
                uv, hz = struct.unpack_from('<QQ', data, 16 + 16 * (ch * dest.count + row))
                dest.hz[row] = hz & 0xffffffff
                dest.mv[row][ch] = (uv // 1000) & 0xffffffff
                dest.sram_mv[row][ch] = max(dest.mv[row][ch], floor)
    out.caps[:] = [7, 5, 5] if revision == 1 else [selected_count - 1, out.cs.count - 1, out.afr.count - 1]
    return out


def indices(bank, count):
    raw = [0, 1] + [count - 1] * 14
    # The independent secondary stream retains a different repeated source row.
    if bank: raw[2:] = [max(1, count - 2)] * 14
    return raw


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cc', default='cc'); p.add_argument('--rustc', default='rustc')
    p.add_argument('--receipt', type=Path); p.add_argument('--mutants', action='store_true')
    p.add_argument('--ubsan', action='store_true')
    a = p.parse_args()
    assert C.sizeof(Bundle) == 2024 and C.sizeof(Tables) == 1764
    pins = json.loads((m.POWER / 'provenance.json').read_text())['exact_files']
    for name, pin in pins.items():
        assert hashlib.sha256((m.POWER / name).read_bytes()).hexdigest() == pin['sha256']
    with tempfile.TemporaryDirectory(prefix='g15c-native-record-cpu-') as directory:
        tmp = Path(directory); objects = tmp / 'objects'; objects.mkdir()
        library, reference = m.build(objects, a.cc, ubsan=a.ubsan)
        lib, ref = C.CDLL(str(library)), C.CDLL(str(reference))
        lib.fixture_call.argtypes = [C.c_uint, C.c_uint64, C.c_uint64, C.c_uint, C.c_void_p, C.c_uint32, C.c_void_p]
        ref.g15c_23j220_decode.argtypes = [C.POINTER(m.Packed), C.POINTER(m.Coefficients)]
        lib.g15c_native_caps.argtypes = [C.c_uint32] * 5 + [C.POINTER(C.c_uint32)]
        cap_cases = 0
        for selector in (0, 0x17, 0x18):
            for revision in (0, 1, 2, 0xffffffff):
                for main_count in (0, 1, 2, 7, 8, 16, 17):
                    for cs_count in (0, 1, 2, 5, 6, 16, 17):
                        for afr_count in (0, 1, 2, 5, 6, 8, 9):
                            caps = (C.c_uint32 * 3)(*[0xa5a5a5a5] * 3)
                            wanted = [7, 5, 5] if revision == 1 else [main_count - 1, cs_count - 1, afr_count - 1]
                            valid = selector == 0x18 and 2 <= main_count <= 16 and 2 <= cs_count <= 16 and 2 <= afr_count <= 8
                            valid = valid and all(v < n for v, n in zip(wanted, [main_count, cs_count, afr_count]))
                            ret = lib.g15c_native_caps(selector, revision, main_count, cs_count, afr_count, caps)
                            assert ret == (0 if valid else -1)
                            assert list(caps) == (wanted if valid else [0xa5a5a5a5] * 3)
                            cap_cases += 1
        stream, cases = bytearray(), []
        refused = 0
        for trial in range(104):
            src, prepared, keep = m.source_case(trial, ref)
            if prepared.source.main_states < 3: continue
            n = 3 + trial % 14
            maps = []
            for bank in range(2):
                raw = indices(bank, prepared.source.main_states)
                raw[n:] = [0] * (16 - n); maps.append(raw)
                src.map_banks[bank][:] = [raw[i] | raw[i + 1] << 16 for i in range(0, 16, 2)]
            for revision in (0, 1):
                want = oracle(src, n, revision)
                valid = all(cap < count for cap, count in zip(want.caps, [n, want.cs.count, want.afr.count]))
                out = Bundle(); C.memset(C.byref(out), 0xa5, C.sizeof(out)); before = bytes(out)
                status = lib.fixture_call(1024 if revision else 0, 3 << 22 | 1 << 24, 0x0800009f,
                                          3, C.byref(src), 0x6034, C.byref(out))
                if not valid:
                    assert status == -1 and bytes(out) == before; refused += 1; continue
                assert status == 0
                assert bytes(out.tables) == bytes(want), 'native imported tables/caps differ from independent oracle'
                stream += bytes(out); cases.append((want, maps, n))
                for flags, error in [(8, -11), (64, -11), (128, -12), (512, -12), (256, -3)]:
                    bad = Bundle(); C.memset(C.byref(bad), 0xa5, C.sizeof(bad)); original = bytes(bad)
                    assert lib.fixture_call(flags, 1 << 22, 0x0800009f, 3, C.byref(src), 0x6034, C.byref(bad)) == error
                    assert bytes(bad) == original; refused += 1
        record = ROOT / 'drivers/gpu/drm/asahi/g15/board/record.rs'
        original = record.read_text(); at = original.rfind('\n}')
        test = (HERE / 'fixture.rs').read_text()
        source = tmp / 'record.rs'; source.write_text(original[:at] + '\n' + test + original[at:])
        harness = tmp / 'harness.rs'
        board = record.parent
        harness.write_text('#![allow(dead_code)]\nmod board {\n' + ''.join(
            '#[path=' + json.dumps(str(board / (name + '.rs'))) + '] pub mod ' + name + ';\n'
            for name in ('template', 'data', 'words')) + '#[path=' + json.dumps(str(source)) + '] pub mod record;\npub(crate) use template::{Line,Template};\n}\n')
        binary = tmp / 'tests'
        m.run([a.rustc, '--edition=2021', '--test', '-O', harness, '-o', binary])
        output = tmp / 'records.bin'
        environment = dict(os.environ, G15_NATIVE_RECORD_OUTPUT=str(output))
        run = subprocess.run([str(binary)], input=bytes(stream), capture_output=True, env=environment, timeout=120)
        assert run.returncode == 0, (run.stdout + run.stderr).decode()
        result = output.read_bytes(); assert len(result) == len(cases) * 0xb480
        checked = 0
        for case, (raw, maps, n) in enumerate(cases):
            b = result[case * 0xb480:(case + 1) * 0xb480]
            def cell(at, expected):
                nonlocal checked
                assert struct.unpack_from('<I', b, at)[0] == expected, ('record cell differs', at)
                checked += 1
            cell(0xb58, n - 1); cell(0x134c, n - 1)
            for k in range(16):
                active = k < n
                pidx, sidx = maps[0][k], maps[1][k]
                cell(0xb5c + 4 * k, raw.main.hz[pidx] // 1000000 if active else 0)
                cell(0xf9c + 4 * k, raw.main.hz[sidx] // 1000000 if active else 0)
                cell(0x1350 + 4 * k, raw.main.hz[sidx] // 1000000 if active else 0)
                for j in range(8):
                    cell(0xb9c + 32 * k + 4 * j, raw.main.mv[pidx][j] if active else 0)
                    cell(0xd9c + 32 * k + 4 * j, raw.main.sram_mv[pidx][j] if active else 0)
                    cell(0x1390 + 32 * k + 4 * j, raw.main.mv[sidx][j] if active and j < 4 else 0)
                    cell(0x1590 + 32 * k + 4 * j, raw.main.sram_mv[sidx][j] if active and j < 4 else 0)
            for raw_aux, base, capacity, cap in [(raw.cs, 0x115c, 16, raw.caps[1]), (raw.afr, 0x12a4, 8, raw.caps[2])]:
                cell(base, raw_aux.count - 1); cell(base + 4, cap)
                for row in range(capacity):
                    live = row < raw_aux.count
                    cell(base + 8 + row * 4, raw_aux.hz[row] // 1000000 if live else 0)
                    for ch in range(2):
                        cell(base + 8 + 4 * capacity + row * 8 + ch * 4, raw_aux.mv[row][ch] if live and ch == 0 else 0)
                        cell(base + 8 + 12 * capacity + row * 8 + ch * 4, raw_aux.sram_mv[row][ch] if live and ch == 0 else 0)
        rejected = []
        if a.mutants:
            mutants = {
                'primary-sram-floor': ('|n| n.main.sram_mv[t.primary[k] as usize][j]', '|_n| v.max(min_sram)'),
                'secondary-sram-floor': ('|n| n.main.sram_mv[t.secondary[k] as usize][j]', '|_n| ss.mv[j].max(min_sram)'),
                'aux-sram-floor': ('|(a, _)| a.sram_mv[k][j]', '|(_a, _)| s.mv[j].max(cs_sram)'),
                'aux-cap-is-top': ('imported.map_or(top, |(_, cap)| cap)', 'top'),
                'sort-native-maps': ('let t = Tables { len: count, primary, secondary };', 'let t = tables(input.main)?;'),
            }
            for name, (old, new) in mutants.items():
                assert original.count(old) == 1, name
                changed = original.replace(old, new)
                source.write_text(changed[:changed.rfind('\n}')] + '\n' + test + changed[changed.rfind('\n}'):])
                m.run([a.rustc, '--edition=2021', '--test', '-O', harness, '-o', binary])
                # These are faulty record serializations, detected by the independent
                # per-cell oracle after a successfully completed Rust build/run.
                attempt = subprocess.run([str(binary), 'native_c_bundle_to_complete_record'], input=bytes(stream),
                                         capture_output=True, env=environment, timeout=120)
                if name == 'sort-native-maps':
                    assert attempt.returncode == 101 and b'native_c_bundle_to_complete_record' in attempt.stdout
                else:
                    assert attempt.returncode == 0
                    faulty = output.read_bytes()
                    ranges = {
                        'primary-sram-floor': [(0xd9c, 0xf9c)],
                        'secondary-sram-floor': [(0x1590, 0x1790)],
                        'aux-sram-floor': [(0x1224, 0x12a4), (0x130c, 0x134c)],
                        'aux-cap-is-top': [(0x1160, 0x1164), (0x12a8, 0x12ac)],
                    }[name]
                    differences = [k % 0xb480 for k, (x, y) in enumerate(zip(faulty, result)) if x != y]
                    assert len(faulty) == len(result) and differences
                    assert all(any(start <= off < end for start, end in ranges) for off in differences)
                rejected.append(name)
        receipt = {'compiler': m.run([a.cc, '--version']).splitlines()[0],
                   'rustc': m.run([a.rustc, '--version']).strip(), 'native_bundles_to_full_records': len(cases),
                   'checked_performance_words': checked, 'atomic_c_refusals': refused,
                   'integer_cap_cases': cap_cases,
                   'rust_geometry_refusals': len(cases) * 8, 'mutants_rejected': rejected,
                   'scope': 'Invented CPU inputs; real source and Rust record; kernel services modeled',
                   'source_sha256': {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest()
                       for f in [record, HERE / 'fixture.rs', Path(__file__).resolve(),
                                 HERE.parent / 'g15-native-power/check.py',
                                 HERE.parent / 'g15-native-power/fixture.c',
                                 HERE.parent / 'g15-native-power/support.h',
                                 record.parent.parent / 'board.rs', record.parent.parent / 'initdata.rs',
                                 *[record.parent / (name + '.rs') for name in ('words', 'template', 'data')],
                                 *[f for f in m.POWER.rglob('*') if f.is_file()]]}}
    if a.receipt: a.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'source_sha256'}, indent=2))


if __name__ == '__main__': main()

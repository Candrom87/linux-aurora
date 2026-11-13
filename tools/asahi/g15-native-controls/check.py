#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Actual native controller parser; invented scalar/zone data, no hardware I/O."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
G15 = ROOT / 'drivers/gpu/drm/asahi/g15'
# Independent expected record order. No production name-conversion helper is
# used to make these input names or the expected output arrays.
FIELDS = '''power-sample-period perf-base-pstate - - avg-power-filter-tc-ms
avg-power-ki-only avg-power-kp avg-power-min-duty-cycle avg-power-target-filter-tc
fast-die0-integral-gain fast-die0-proportional-gain fast-die0-prop-tgt-delta
perf-boost-ce-step perf-boost-min-util perf-filter-drop-threshold
perf-filter-time-constant perf-filter-time-constant2 perf-integral-gain
perf-integral-gain2 perf-integral-min-clamp perf-proportional-gain
perf-proportional-gain2 perf-reset-iters perf-tgt-utilization
ppm-filter-time-constant-ms ppm-ki ppm-kp pwr-filter-time-constant
pwr-integral-gain pwr-integral-min-clamp pwr-min-duty-cycle
pwr-proportional-gain pwr-sample-period-aic-clks se-engagement-criteria
se-filter-time-constant se-filter-time-constant-1 se-inactive-threshold
se-ki se-ki-1 se-kp se-kp-1 se-reset-criteria se-tgt'''.split()
assert len(FIELDS) == 43
NAMES = [None if name == '-' else 'gpu-' + name for name in FIELDS]
IDLE = 'gpu-idleoff-standby-timer'
DEFAULTS = {NAMES[11]: 0, NAMES[22]: 6}
POWER = [0, IDLE, 4, 5, 6, 7, 8, 10, 9, 26, 25]


def block(text, marker):
    start = text.index(marker)
    end = text.index('{', start) + 1
    depth = 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


def node(name, props=(), children=()):
    return [list(props) + [('name', name.encode() + b'\0')], list(children)]


def encode(tree):
    props, children = tree
    data = struct.pack('<II', len(props), len(children))
    for key, value in props:
        data += key.encode().ljust(32, b'\0') + struct.pack('<I', len(value))
        data += value + bytes(-len(value) % 4)
    return data + b''.join(encode(child) for child in children)


def zone_key(cell, row):
    return 'gpu-power-zone-' + ['target-', 'target-offset-', 'filter-tc-'][cell] + str(row)


def fixture(zones=0, optional=True):
    values = {name: 100 + index for index, name in enumerate(NAMES) if name}
    values[IDLE] = 99
    values[NAMES[0]] = 8
    if not optional:
        for key in DEFAULTS:
            del values[key]
    props = [('compatible', b'gpu,t6034\0')]
    props += [(key, struct.pack('<I', value)) for key, value in values.items()]
    for row in range(zones):
        props += [(zone_key(cell, row), struct.pack('<I', value))
                  for cell, value in enumerate([5000 + row, 200 + row, 8 + row])]
    return node('root', children=[node('arm-io', children=[node('sgx', props)])])


def props(tree):
    return tree[1][0][1][0][0]


def replace(tree, name, value):
    props(tree)[:] = [(key, value if key == name else old) for key, old in props(tree)]


def expected(tree):
    # Inputs are known-valid here. Expected words are direct source integers in
    # independent fixed order; this is not an admission oracle for malformed ADT.
    values = {name: struct.unpack('<I', value)[0] for name, value in props(tree)
              if name not in ['name', 'compatible']}
    record = [values.get(name, DEFAULTS.get(name, 0)) if name else 0 for name in NAMES]
    power = [values[index] if isinstance(index, str) else record[index] for index in POWER]
    zones = []
    for row in range(5):
        if zone_key(0, row) not in values:
            break
        zones.extend(values[zone_key(cell, row)] for cell in range(3))
    return record + power + [len(zones) // 3] + zones + [0] * (15 - len(zones))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rustc', default='rustc')
    parser.add_argument('--mutants', action='store_true')
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    cases = []

    def add(tree, valid=False, chip=0x6034):
        cases.append((encode(tree) if isinstance(tree, list) else tree,
                      valid, chip, expected(tree) if valid else []))

    for count in range(6):
        for optional in [False, True]:
            tree = fixture(count, optional)
            add(tree, True)
            props(tree).reverse()
            add(tree, True)
    first = fixture(2)
    keys = [name for name in NAMES if name] + [IDLE]
    keys += [zone_key(cell, row) for row in range(2) for cell in range(3)]
    for name in keys:
        original = next(value for key, value in props(first) if key == name)
        for value in [b'', original[:1], original[:2], original[:3], original + b'X']:
            tree = copy.deepcopy(first)
            replace(tree, name, value)
            add(tree)
        tree = copy.deepcopy(first)
        props(tree).append((name, original))
        add(tree)
        tree = copy.deepcopy(first)
        props(tree)[:] = [(key, value) for key, value in props(tree) if key != name]
        add(tree, name in DEFAULTS)
    for value in [0, 1, 0x01020304, 0xffffffff]:
        tree = fixture()
        for key in DEFAULTS:
            replace(tree, key, struct.pack('<I', value))
        add(tree, True)
    tree = fixture()
    replace(tree, NAMES[0], bytes(4))
    add(tree)
    for chip in [0, 0x6030, 0x6031, 0x6035]:
        add(first, chip=chip)
    for value in [b'gpu,t6031\0', b'gpu,t6034', b'gpu,t6034\0extra']:
        tree = copy.deepcopy(first)
        replace(tree, 'compatible', value)
        add(tree)
    for name in [NAMES[0], NAMES[42], IDLE]:
        tree = copy.deepcopy(first)
        props(tree)[:] = [(key + '-x' if key == name else key, value) for key, value in props(tree)]
        add(tree)
    for missing in [0, 1]:
        tree = copy.deepcopy(first)
        props(tree)[:] = [(key, value) for key, value in props(tree)
                         if key not in [zone_key(cell, missing) for cell in range(3)]]
        add(tree, missing == 1)
    add(fixture(6))
    for suffix in ['5', '6', '9', '10', '00', '-1', 'x']:
        for cell in range(3):
            tree = copy.deepcopy(first)
            name = zone_key(cell, 0)[:-1] + suffix
            props(tree).append((name, struct.pack('<I', 1)))
            add(tree)
    for key, value in [(zone_key(1, 0), 6000), (zone_key(2, 0), 0)]:
        tree = copy.deepcopy(first)
        replace(tree, key, struct.pack('<I', value))
        add(tree)
    for path in [[], [0]]:
        tree = copy.deepcopy(first)
        parent = tree
        for index in path:
            parent = parent[1][index]
        parent[1].append(copy.deepcopy(parent[1][0]))
        add(tree)
    raw = encode(first)
    for length in range(len(raw)):
        add(raw[:length])
    add(raw + bytes(0x200001 - len(raw)))
    stream = b''.join(struct.pack('<III', chip, valid, len(data)) + data +
                      (struct.pack('<70I', *words) if valid else b'')
                      for data, valid, chip, words in cases)
    adt = (G15 / 'adt.rs').read_text()
    helpers = '\n'.join(block(adt, marker) for marker in ["struct Adt<'a>", 'fn le32(',
                        'fn le64(', 'fn strncmp_eq(', "impl Adt<'_>"])
    controls = (G15 / 'adt/native_controls.rs').read_text()
    template = (HERE / 'fixture.rs').read_text().replace('ADT_HELPERS', helpers)
    for token, rel in [('WORDS', 'board/words.rs'), ('RECORD', 'board/record.rs'), ('NATIVE', 'adt/native.rs')]:
        template = template.replace(token, str(G15 / rel))
    rejected = []
    with tempfile.TemporaryDirectory(prefix='g15-native-controls-') as directory:
        temp = Path(directory)

        def run(label, text):
            source = temp / (label + '-controls.rs')
            source.write_text(text)
            fixture_source = temp / (label + '.rs')
            fixture_source.write_text(template.replace('CONTROLS', str(source)))
            binary = temp / label
            compiled = subprocess.run([args.rustc, '--edition=2021', '-O', '-Dwarnings',
                                       fixture_source, '-o', binary], capture_output=True, text=True)
            assert compiled.returncode == 0, compiled.stdout + compiled.stderr
            return subprocess.run([binary], input=stream, capture_output=True, timeout=60)

        result = run('actual', controls)
        assert result.returncode == 0, result.stdout + result.stderr
        if args.mutants:
            for label, old, new in [
                ('chip', 'chip != 0x6034 ||', '(false && chip != 0x6034) ||'),
                ('scalar-shape', 'bytes.len() != 4', 'bytes.len() < 4'),
                ('scalar-endian', 'le32(bytes, 0)', 'Some(u32::from_be_bytes(bytes[..4].try_into().unwrap()))'),
                ('se-target-alias', 'suffix == b"se-target"', 'suffix == b"unused-target"'),
                ('optional-overrides', 'Some(bytes) => scalar(bytes)?,', 'Some(bytes) => default.unwrap_or(scalar(bytes)?),'),
                ('wrong-source', 'core::ptr::eq(self.source, data)', 'self.source == data'),
                ('power-order', 'values[index]', '{ let _ = index; values[0] }'),
                ('zone-gap', 'if gap ||', 'if (false && gap) ||'),
                ('zone-names', 'zone_names(&adt, sgx)?;', 'if false { zone_names(&adt, sgx)?; }'),
                ('zone-zero-tc', 'zones[3 * index + 2] == 0', '(false && zones[3 * index + 2] == 0)'),
                ('zone-offset', 'zones[3 * index] < zones[3 * index + 1]', '(false && zones[3 * index] < zones[3 * index + 1])'),
            ]:
                assert controls.count(old) == 1, (label, controls.count(old))
                attempt = run(label, controls.replace(old, new, 1))
                assert attempt.returncode == 101 and b'panicked at' in attempt.stderr, (label, attempt.stderr)
                rejected.append(label)
        escape = temp / 'escape.rs'
        escape.write_text(template.replace('CONTROLS', str(temp / 'actual-controls.rs')) +
                          '\nfn escape() -> g15::adt::native_controls::Settings<\'static> {'
                          ' let bytes = vec![0u8; 8]; g15::adt::parse(&bytes, 0x6034).unwrap() }\n')
        compiled = subprocess.run([args.rustc, '--edition=2021', escape, '-o', temp / 'escape'],
                                  capture_output=True, text=True)
        assert compiled.returncode != 0 and 'E0515' in compiled.stderr, compiled.stderr
    files = [G15 / rel for rel in ['adt.rs', 'adt/native.rs', 'adt/native_controls.rs',
                                   'board/record.rs', 'board/words.rs']]
    files += [Path(__file__).resolve(), HERE / 'fixture.rs']
    receipt = {'scope': __doc__, 'frames': len(cases), 'accepted': sum(row[1] for row in cases),
               'passed': True, 'mutants_rejected': rejected, 'source_lifetime_escape': 'E0515',
               'source_sha256': {str(file.relative_to(ROOT)): hashlib.sha256(file.read_bytes()).hexdigest()
                                 for file in files}}
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({key: value for key, value in receipt.items() if key != 'source_sha256'}, indent=2))


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise the production MTR parser using invented ADT and polynomial bytes."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
G15 = ROOT/'drivers/gpu/drm/asahi/g15'
from metadata_fixture import node, encode, selected, prop, block

def cases():
    def record(sensor, payload):
        return struct.pack('<II', sensor, len(payload)) + payload
    def tree(raw):
        return node(b'root', children=[node(b'arm-io', children=[
            node(b'sgx', [(b'compatible', b'gpu,t6034\0')]),
            node(b'pmgr', [(b'compatible', b'pmgr1,t6031\0'),
                           (b'mtr-polynom-fuse-agx', raw)])])])
    result = []
    def valid(label, ids, payloads=None):
        payloads = payloads or [bytes(4)]*len(ids)
        raw = b''.join(record(sensor, payload) for sensor, payload in zip(ids, payloads))
        mask = sum(1 << sensor for sensor in set(ids))
        limbs = [(mask >> shift) & 0xffffffff for shift in (96, 64, 32, 0)]
        result.append((label, encode(tree(raw)), 0x6034, (limbs, len(ids), len(set(ids)))))
    # Payloads contain apparent IDs deliberately: they must never become membership.
    for sensor in range(128):
        valid('single-id', [sensor], [struct.pack('<4I', 17, 63, 96, 127)])
    valid('all-ids', list(range(128)))
    valid('duplicates', [0, 0, 31, 32, 63, 64, 95, 96, 127, 127])
    valid('zero-payloads', [0, 32, 64, 96], [b'']*4)
    valid('max-property', [77], [bytes(0x100000-8)])
    rng = random.Random(155)
    for _ in range(64):
        ids = [rng.randrange(128) for _ in range(rng.randrange(1, 65))]
        valid('varied-records', ids, [rng.randbytes(4*rng.randrange(9)) for _ in ids])
    base = tree(record(7, struct.pack('<I', 127)))
    expected = ([0, 0, 0, 1 << 7], 1, 1)
    def change(label, edit, outcome=None):
        value = copy.deepcopy(base)
        edit(value)
        result.append((label, encode(value), 0x6034, outcome))
    key = b'mtr-polynom-fuse-agx'
    for raw in (b'', bytes(4), bytes(7), bytes(9),
                record(128, b''), record(0xffffffff, b''),
                struct.pack('<II', 7, 3)+bytes(4),
                record(7, bytes(3))+record(8, bytes(1)),
                struct.pack('<II', 7, 8)+bytes(4),
                struct.pack('<II', 7, 0xfffffffc),
                record(7, bytes(4))+bytes(4),
                record(7, bytes(0x100000-4))):
        change('invalid-record', lambda t, raw=raw: prop(selected(t, b'pmgr'), key, raw))
    for chip in (0, 0x6030, 0x6031, 0x6035):
        result.append(('wrong-chip', encode(base), chip, None))
    for name in (b'arm-io', b'sgx', b'pmgr'):
        change('missing-child', lambda t, n=name: prop(selected(t, n), b'name', b'other\0'))
        change('duplicate-name', lambda t, n=name: selected(t, n)['p'].append((b'name', n+b'\0')))
        def duplicate(t, name=name):
            parent = t if name == b'arm-io' else selected(t, b'arm-io')
            parent['c'].append(copy.deepcopy(selected(t, name)))
        change('duplicate-child', duplicate)
    for owner in (b'sgx', b'pmgr'):
        for value in (b'', b'wrong\0', b'gpu,t6034\0suffix'):
            change('wrong-compatible', lambda t,o=owner,v=value: prop(selected(t,o),b'compatible',v))
        change('duplicate-compatible', lambda t,o=owner: selected(t,o)['p'].append(
            next(p for p in selected(t,o)['p'] if p[0] == b'compatible')))
    change('missing-polynomial', lambda t: selected(t,b'pmgr')['p'].pop())
    change('duplicate-polynomial', lambda t: selected(t,b'pmgr')['p'].append((key,record(8,b''))))
    change('prefix-polynomial', lambda t: selected(t,b'pmgr')['p'].__setitem__(-1,(key+b'-x',record(8,b''))))
    change('unrelated-property', lambda t: selected(t,b'pmgr')['p'].append((b'other',bytes(7))), expected)
    raw = encode(base)
    for size in (0, 4, 8, 35, len(raw)-1, len(raw)-4):
        result.append(('truncated-tree', raw[:size], 0x6034, None))
    result.append(('oversize-copy', raw+bytes(0x200001-len(raw)), 0x6034, None))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rustc', default='rustc')
    p.add_argument('--receipt', type=Path)
    p.add_argument('--mutants', action='store_true')
    args = p.parse_args()
    files = [G15/'adt.rs', G15/'adt/native.rs', G15/'adt/native_mtr.rs',
             HERE/'fixture.rs', Path(__file__).resolve(),
             HERE/'metadata_fixture.py']
    adt, native, source, template = [f.read_text() for f in files[:4]]
    assert 'native_mtr::Membership::new(&self.data, chip)' in adt
    helpers = '\n'.join(block(adt,s) for s in ("struct Adt<'a>", 'fn le32(', 'fn le64(',
                                               'fn strncmp_eq(', "impl Adt<'_>"))
    functions = '\n'.join(block(native,s) for s in ('pub(super) fn property',
                                                   'pub(super) fn required', 'pub(super) fn child'))
    template = template.replace('ADT_HELPERS',helpers).replace('NATIVE_HELPERS',functions)
    examples = cases()
    stream = bytearray(struct.pack('<I',len(examples)))
    for _,data,chip,expected in examples:
        limbs, records, unique = expected or ([0]*4, 0, 0)
        stream += struct.pack('<9I',chip,int(expected is not None),len(data),*limbs,records,unique)+data
    with tempfile.TemporaryDirectory(prefix='g15-native-mtr-') as directory:
        tmp = Path(directory)
        def run(label, old=None, new=None):
            text = source
            if old is not None:
                assert text.count(old) == 1, label
                text = text.replace(old,new,1)
            module = tmp/(label+'-mtr.rs'); module.write_text(text)
            fixture = tmp/(label+'.rs'); binary = tmp/label
            fixture.write_text(template.replace('MTR_SOURCE',str(module)).replace('LIFETIME_PROBE',''))
            built = subprocess.run([args.rustc,'--edition=2021','-O','-Dwarnings',str(fixture),'-o',str(binary)],
                                   capture_output=True,text=True,timeout=120)
            assert built.returncode == 0, built.stdout+built.stderr
            return subprocess.run([str(binary)],input=stream,capture_output=True,timeout=60)
        actual = run('actual')
        assert actual.returncode == 0, (actual.stdout+actual.stderr).decode()
        probe = tmp/'lifetime.rs'
        probe.write_text(template.replace('MTR_SOURCE',str(tmp/'actual-mtr.rs')).replace('LIFETIME_PROBE',
            "fn escape<'a>(bytes: Vec<u8>) -> native_mtr::Membership<'a> { native_mtr::Membership::new(&bytes,0x6034).unwrap() }"))
        rejected = subprocess.run([args.rustc,'--edition=2021',str(probe),'-o',str(tmp/'lifetime')],
                                  capture_output=True,text=True,timeout=120)
        assert rejected.returncode != 0 and 'E0515' in rejected.stderr, rejected.stderr
        mutants = []
        if args.mutants:
            for label,old,new in [
                ('ignore-chip','chip != 0x6034 ||','(false && chip != 0x6034) ||'),
                ('ignore-property-limit','raw.len() > 0x10_0000','false && raw.len() > 0x10_0000'),
                ('ignore-ID-bound','id >= 128','false && id >= 128'),
                ('wrong-bit','mask |= 1u128 << id;','mask |= 1u128 << (id % 32);'),
                ('lose-members','mask |= 1u128 << id;','mask = 1u128 << id;'),
                ('count-duplicates','self.mask.count_ones()','self.records as u32'),
                ('wrong-cell-order','(self.mask >> 96) as u32','self.mask as u32'),
                ('ignore-alignment','size & 3 != 0','false && size & 3 != 0'),
            ]:
                result = run(label,old,new)
                assert result.returncode == 101, (label,result.returncode)
                mutants.append(label)
    receipt = {'cases':len(examples), 'valid':sum(x[3] is not None for x in examples),
               'refused':sum(x[3] is None for x in examples), 'mutants_rejected':mutants,
               'source_lifetime_escape':'E0515', 'scope':'production parser and actual ADT helpers; invented data only; no device I/O',
               'source_sha256':{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}
    if args.receipt: args.receipt.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k != 'source_sha256'},indent=2))


if __name__ == '__main__': main()

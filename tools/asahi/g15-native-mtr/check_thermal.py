#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Actual Rust selection + MTR parser + C identity/mask model, invented inputs."""
import argparse
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
from metadata_fixture import node, encode, block

def cases():
    contract = 0x23c22001
    hotspot = 0x802a002b802a002b  # Version-scoped code constant, not captured data.
    def limbs(value): return [(value >> shift) & 0xffffffff for shift in (96,64,32,0)]
    def tree(mask):
        records = b''.join(struct.pack('<4I',i,8,0xffffffff,127) for i in range(128) if mask >> i & 1)
        return encode(node(b'root',children=[node(b'arm-io',children=[
            node(b'sgx',[(b'compatible',b'gpu,t6034\0')]),
            node(b'pmgr',[(b'compatible',b'pmgr1,t6031\0'),(b'mtr-polynom-fuse-agx',records)])])]))
    result = []
    def add(label, mask, identity=None, tag=contract, valid=True):
        identity = identity or [0x07040000,0x0001040a,0,0,0,0]
        expected = limbs(hotspot)*2 + limbs(mask)
        result.append((label,valid,tag,identity,tree(mask),expected))
    for major in range(16):
        for minor in range(16):
            for low in (0,255):
                add('revision-context',hotspot | (1 << 127),
                    [0x07040000 | major << 12 | minor << 8 | low,0x0001040a,0,1,2,3])
    add('minimal',hotspot)
    add('all-members',(1 << 128)-1)
    for bit in range(128):
        if hotspot >> bit & 1:
            add('missing-required',hotspot ^ (1 << bit),valid=False)
        else:
            add('extra-member',hotspot | (1 << bit))
    rng = random.Random(156)
    for _ in range(128): add('wide-membership',hotspot | rng.getrandbits(128))
    for tag in (0,1,contract ^ 1,0xffffffff): add('wrong-contract',hotspot,tag=tag,valid=False)
    for index, value in [(0,0),(0,0x06040000),(0,0x07030000),
                         (1,0),(1,0x0002040a),(1,0x0001030a),(1,0x00010409)]:
        identity = [0x07040000,0x0001040a,0,0,0,0];identity[index] = value
        add('wrong-identity',hotspot,identity,valid=False)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rustc',default='rustc');p.add_argument('--cc',default='cc')
    p.add_argument('--receipt',type=Path);p.add_argument('--mutants',action='store_true')
    args = p.parse_args()
    files = [G15/'adt.rs',G15/'adt/native.rs',G15/'adt/native_mtr.rs',
             G15/'board/native/thermal.rs',G15/'power/native_caps.c',G15/'power/native.h',
             HERE/'thermal-fixture.rs',Path(__file__).resolve(),
             G15/'power/g15c_23j220_power.h',G15/'power/native_import.h',
             HERE.parent/'test-support/expected-failure.h',
             HERE/'metadata_fixture.py']
    adt,native,mtr,thermal,caps,header,template = [f.read_text() for f in files[:7]]
    helpers = '\n'.join(block(adt,s) for s in ("struct Adt<'a>",'fn le32(','fn le64(',
                                               'fn strncmp_eq(',"impl Adt<'_>"))
    functions = '\n'.join(block(native,s) for s in ('pub(super) fn property','pub(super) fn required','pub(super) fn child'))
    template = template.replace('ADT_HELPERS',helpers).replace('NATIVE_HELPERS',functions)
    examples = cases();stream = bytearray(struct.pack('<I',len(examples)))
    for _,valid,tag,identity,data,expected in examples:
        stream += struct.pack('<21I',int(valid),tag,len(data),*identity,*expected)+data
    with tempfile.TemporaryDirectory(prefix='g15-native-thermal-') as directory:
        tmp = Path(directory);(tmp/'mtr.rs').write_text(mtr)
        def build(label, csource=caps, rustsource=thermal, enabled=True, probe=''):
            cfile = tmp/(label+'.c'); cfile.write_text(csource)
            obj = tmp/(label+'.o');lib = tmp/('lib'+label+'.a')
            command = [args.cc,'-std=c11','-O2','-fno-strict-aliasing','-I'+str(G15/'power'),
                       '-include',str(HERE.parent/'test-support/expected-failure.h'),'-c',str(cfile),'-o',str(obj)]
            compiled = subprocess.run(command,capture_output=True,text=True,timeout=120)
            assert compiled.returncode == 0, compiled.stdout+compiled.stderr
            subprocess.run(['ar','rcs',str(lib),str(obj)],check=True,capture_output=True)
            module = tmp/(label+'-thermal.rs');module.write_text(rustsource)
            fixture = tmp/(label+'.rs');binary = tmp/label
            fixture.write_text(template.replace('MTR_SOURCE',str(tmp/'mtr.rs'))
                               .replace('THERMAL_SOURCE',str(module)).replace('LIFETIME_PROBE',probe))
            cmd = [args.rustc,'--edition=2021','-O','-Dwarnings',str(fixture),'-L',str(tmp),'-lstatic='+label,'-o',str(binary)]
            if enabled: cmd += ['--cfg','CONFIG_DRM_ASAHI_G15_NATIVE_POWER']
            compiled = subprocess.run(cmd,capture_output=True,text=True,timeout=120)
            if probe:
                assert compiled.returncode != 0 and 'E0515' in compiled.stderr, compiled.stderr
                return None
            assert compiled.returncode == 0, compiled.stdout+compiled.stderr
            return subprocess.run([str(binary)],input=stream,capture_output=True,timeout=60)
        actual = build('actual');assert actual.returncode == 0,(actual.stdout+actual.stderr).decode()
        disabled = build('disabled',enabled=False);assert disabled.returncode == 0,(disabled.stdout+disabled.stderr).decode()
        build('identity_lifetime',probe="fn escape<'a>(fw: &'a g15::identity::NativeFirmware, m: &'a g15::adt::native_mtr::Membership<'a>) -> g15::board::native::thermal::Masks<'a> { let id=[0;6]; g15::board::native::thermal::select(fw,m,&id).unwrap() }")
        build('firmware_lifetime',probe="fn escape<'a>(m: &'a g15::adt::native_mtr::Membership<'a>, id: &'a [u32;6]) -> g15::board::native::thermal::Masks<'a> { let fw=g15::identity::NativeFirmware{build:g15::identity::Build{native_board_contract:Some(0x23c22001)}}; g15::board::native::thermal::select(&fw,m,id).unwrap() }")
        build('membership_lifetime',probe="fn escape<'a>(fw: &'a g15::identity::NativeFirmware, bytes: &'a [u8], id: &'a [u32;6]) -> g15::board::native::thermal::Masks<'a> { let m=g15::adt::membership(bytes); g15::board::native::thermal::select(fw,&m,id).unwrap() }")
        mutants = []
        if args.mutants:
            for label,where,old,new in [
                ('ignore-contract','rust','firmware.build().native_board_contract != Some(NATIVE_CONTRACT)','false && firmware.build().native_board_contract != Some(NATIVE_CONTRACT)'),
                ('ignore-subset','c','if ((fast_die[i] & ~enabled[i]) || (sochot[i] & ~enabled[i]))','if (0 && ((fast_die[i] & ~enabled[i]) || (sochot[i] & ~enabled[i])))'),
                ('wrong-primary','c','result[0][i] = fast_die[i];','result[0][i] = enabled[i];'),
                ('wrong-sochot','c','result[1][i] = sochot[i];','result[1][i] = enabled[i];'),
                ('collapse-alarm','c','result[2][i] = enabled[i];','result[2][i] = fast_die[i];'),
                ('wrong-cell-order','c','result[2][i] = enabled[i];','result[2][i] = enabled[3-i];'),
                ('partial-publication','c','result[2][i] = enabled[i];','result[2][i] = enabled[i]; masks[2][i] = enabled[i];'),
            ]:
                source = thermal if where == 'rust' else caps
                assert source.count(old) == 1,label
                source = source.replace(old,new,1)
                result = build(label,rustsource=source) if where == 'rust' else build(label,csource=source)
                assert result.returncode == 101,(label,result.returncode,result.stderr.decode())
                mutants.append(label)
    receipt = {'pipeline_cases':len(examples),'valid':sum(x[1] for x in examples),
               'refused':sum(not x[1] for x in examples),'raw_C_atomic_refusals':22,
               'feature_disabled_cases':len(examples),'lifetime_escape_checks':['identity E0515','firmware E0515','membership E0515'],
               'mutants_rejected':mutants,'compiler':args.cc,
               'scope':'Actual MTR/ADT parsing, Rust thermal wrapper and C context/masks; invented bytes; firmware token accessor modeled; no I/O',
               'source_sha256':{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}
    if args.receipt:args.receipt.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k != 'source_sha256'},indent=2))


if __name__ == '__main__': main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Execute actual native/legacy firmware identity with modeled copy/hash services."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import resource
import struct
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
G15=ROOT/'drivers/gpu/drm/asahi/g15'
def block(text, marker):
    start=text.index(marker); end=text.index('{',start)+1; depth=1
    while depth:
        depth+=(text[end]=='{')-(text[end]=='}');end+=1
    return text[start:end]
import regions_cases


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rustc',default='rustc')
    parser.add_argument('--mutants',action='store_true')
    parser.add_argument('--receipt',type=Path)
    parser.add_argument('--baseline-identity',type=Path,
                        help='Require the prior identity implementation to fail the current fixture')
    args=parser.parse_args()
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    files=[G15/'identity.rs',G15/'identity/lookup.rs',G15/'identity/matching.rs',G15/'hw.rs',
           G15/'hw/t6031.rs',G15/'boot.rs',G15/'board/record.rs',HERE/'fixture.rs',Path(__file__).resolve(),
           Path(__file__).resolve(),HERE/'README.md',G15/'adt/source.rs',G15/'adt.rs',
           G15/'adt/native.rs',G15/'adt/native_firmware.rs',HERE/'regions_cases.py',HERE/'regions_fixture.rs',G15/'hw/t6034.rs']
    identity,lookup,matching,hw,_,boot,record,template=[p.read_text() for p in files[:8]]
    tag=re.search(r'(?m)^pub\(crate\) const NATIVE_CONTRACT: u32 = [^;]+;',record)
    assert tag
    template=template.replace('FIRMWARE_BUILD_TYPE',block(hw,'pub(crate) struct FirmwareBuild {'))
    template=template.replace('NATIVE_CONTRACT',tag.group(),1)
    template=template.replace('FIRMWARE_MEMORY_CONSTANTS','\n'.join(
        re.search(r'pub\(crate\) const '+name+r': [^;]+;',hw).group() for name in ['PAGE','UPPER','PA_MASK']))
    adt=(G15/'adt.rs').read_text();native=(G15/'adt/native.rs').read_text()
    helpers='\n'.join(block(adt,marker) for marker in ["struct Adt<'a>",'fn le32(','fn le64(',
        'fn strncmp_eq(',"impl Adt<'_>"])
    template=template.replace('ADT_HELPERS',helpers).replace('NATIVE_HELPERS','\n'.join(
        block(native,marker) for marker in ['pub(super) fn property','pub(super) fn required',
                                          'pub(super) fn child','pub(super) fn u32_property']))
    template=template.replace('NATIVE_REGION_METHOD',block(adt,'    pub(crate) fn check_firmware_regions('))
    template=template.replace('NATIVE_ADT_BYTES',','.join(str(value) for value in regions_cases.encode(regions_cases.base())))
    template=template.replace('REGION_CASES',(HERE/'regions_fixture.rs').read_text())
    stream=bytearray();cases=regions_cases.cases()
    for _,data,chip,text,region_data,want in cases:
        stream+=struct.pack('<II4QI',chip,want,*text,*region_data,len(data))+data
    source_identity=(G15/'adt/source.rs').read_text()
    snapshot_line='    let snapshot = adt.native.as_ref().ok_or(ENODEV)?;'
    native_body=block(identity,'pub(crate) fn native_firmware(')
    build_at=native_body.index('    let build = firmware_checked(')
    source_part=native_body[native_body.index(snapshot_line):build_at]
    build_part=native_body[build_at:native_body.index('    Ok(NativeFirmware')]
    delayed=source_part+build_part;delayed_new=build_part+source_part
    region_call='    snapshot.check_firmware_regions(chip, &adt.text, &adt.data)?;\n'
    late_region=identity.replace(region_call,'',1).replace('    Ok(NativeFirmware',region_call+'    Ok(NativeFirmware',1)
    # The current real images remain legacy-only; no native image/profile is
    # selected merely by adding this policy. Their tuple bytes stay unchanged.
    assert (hw+files[4].read_text()+files[-1].read_text()).count('native_board_contract: None,')==7
    admit=block(boot,'pub(crate) fn admit(')
    assert admit.index('identity::native_firmware(')<admit.index('initdata::prepare_board(')<admit.index('regs::Regs::new(')
    assert 'let native_firmware = if cfg.board == hw::Board::DeviceTreeNative' in admit
    with tempfile.TemporaryDirectory(prefix='g15-native-firmware-') as directory:
        tmp=Path(directory)
        def run(label,change=None,baseline=None):
            sources={'identity':identity,'lookup':lookup,'matching':matching,'source':source_identity,
                     'regions':(G15/'adt/native_firmware.rs').read_text()}
            if baseline is not None:sources['identity']=baseline
            if change:
                key,old,new=change
                assert sources[key].count(old)==1,(label,sources[key].count(old))
                sources[key]=sources[key].replace(old,new,1)
            paths={key:tmp/(label+'-'+key+'.rs') for key in sources}
            sources['identity']=sources['identity'].replace('mod lookup;', '#[path="'+str(paths['lookup'])+'"] mod lookup;')
            sources['identity']=sources['identity'].replace('mod matching;', '#[path="'+str(paths['matching'])+'"] mod matching;')
            for key,text in sources.items():paths[key].write_text(text)
            source,binary=tmp/(label+'.rs'),tmp/label
            source.write_text(template.replace('IDENTITY_SOURCE',str(paths['identity'])).replace('SOURCE_IDENTITY',str(paths['source']))
                .replace('NATIVE_REGIONS_SOURCE',str(paths['regions'])))
            compiled=subprocess.run([args.rustc,'--edition=2021','-O','-Dwarnings',str(source),'-o',str(binary)],capture_output=True,text=True,timeout=120)
            assert compiled.returncode==0,label+'\n'+compiled.stdout+compiled.stderr
            return subprocess.run([str(binary)],input=bytes(stream),capture_output=True,timeout=60)
        result=run('actual');assert result.returncode==0,result.stdout+result.stderr
        baseline_rejected=False
        baseline_sha256=None
        baseline_failure_kind=None
        if args.baseline_identity:
            baseline_bytes=args.baseline_identity.read_bytes()
            baseline_sha256=hashlib.sha256(baseline_bytes).hexdigest()
            try:
                prior=run('prior-identity',baseline=baseline_bytes.decode())
            except AssertionError as error:
                assert 'E0599' in str(error),str(error)
                baseline_failure_kind='compiler E0599: prior source lacks affiliation API; not executed'
            else:
                assert prior.returncode==101 and any(message in prior.stderr.decode() for message in
                    ['mapping before admission','native region entry','native region actor order']),prior.stdout+prior.stderr
                baseline_failure_kind='ordinary fixture exit101'
            baseline_rejected=True
        forged=tmp/'forged-token.rs'
        forged.write_text(template.replace('IDENTITY_SOURCE',str(tmp/'actual-identity.rs')).replace('SOURCE_IDENTITY',str(tmp/'actual-source.rs'))
            .replace('NATIVE_REGIONS_SOURCE',str(tmp/'actual-regions.rs'))+
            '\nfn forge(build: &\'static hw::FirmwareBuild, snapshot:&adt::NativeAdt, dev:&device::Device) -> identity::NativeFirmware { identity::NativeFirmware { build, source:snapshot.source_identity().clone(), dev:dev.into() } }\n')
        invalid=subprocess.run([args.rustc,'--edition=2021','-O',str(forged),'-o',str(tmp/'forged-token')],capture_output=True,text=True,timeout=120)
        assert invalid.returncode!=0 and 'E0451' in invalid.stderr,invalid.stdout+invalid.stderr
        rejected=[]
        if args.mutants:
            for label,key,old,new in [
                ('native-consults-prefix','identity','        builds,\n        0,','        builds,\n        super::params::accept_text_prefix(),'),
                ('native-ignores-contract','identity','b.native_board_contract == Some(contract)','(true || b.native_board_contract == Some(contract))'),
                ('native-accepts-unrestricted-image','identity','b.machine == Some(c_str!("apple,j514m"))','(true || b.machine == Some(c_str!("apple,j514m")))'),
                ('native-ignores-chip','identity','chip != 0x6034 ||','(false && chip != 0x6034) ||'),
                ('native-ignores-machine','identity','!glue::machine(c_str!("apple,j514m"))','(false && !glue::machine(c_str!("apple,j514m")))'),
                ('copies-before-source-owner','identity',delayed,delayed_new),
                ('legacy-loses-prefix','identity','firmware_checked(dev, adt, builds, prefix, None)','firmware_checked(dev, adt, builds, 0, None)'),
                ('ignore-machine-scope','identity','b.machine.map_or(true, glue::machine)','(true || b.machine.map_or(true, glue::machine))'),
                ('bind-wrong-pinned-entry','identity','builds.get(i).ok_or(EINVAL)?','builds.get(i - i).ok_or(EINVAL)?'),
                ('skip-entropy-normalization','identity','copy[at + 8..at + 16].fill(0);','copy[at + 8..at + 16].fill(0xe7);'),
                ('leak-temporary-mapping','identity','unsafe { bindings::memunmap(map) };','if false { unsafe { bindings::memunmap(map) }; }'),
                ('prefix-is-full-digest','matching','expected_digest == actual_digest','expected_digest[..8] == actual_digest[..8]'),
                ('ignore-data-size','matching','expected_sizes == actual_sizes','expected_sizes.0 == actual_sizes.0'),
                ('skip-native-preflight','identity','if native_contract.is_some() && !builds.iter().any','if false && native_contract.is_some() && !builds.iter().any'),
                ('skip-preflight-text-size','identity','b.text_size == text_size','(true || b.text_size == text_size)'),
                ('skip-preflight-data-size','identity','b.data_size == data_size','(true || b.data_size == data_size)'),
                ('skip-preflight-marker-alignment','identity','b.marker % 8 == 0','(true || b.marker % 8 == 0)'),
                ('skip-preflight-marker-bounds','identity','u64::from(b.marker) + 16 <= text_size','(true || u64::from(b.marker) + 16 <= text_size)'),
                ('ignore-source-affiliation','identity','self.source.same(snapshot.source_identity())','self.source.same(snapshot.source_identity()) || true'),
                ('ignore-consumer-affiliation','identity','self.dev.as_raw() == dev.as_raw()','(self.dev.as_raw() == dev.as_raw() || true)'),
                ('collapse-copy-identities','source','Arc::ptr_eq(&self.0, &other.0)','Arc::ptr_eq(&self.0, &other.0) || true'),
                ('skip-region-source','identity',region_call,''),
                ('copy-before-region-source','identity',identity,late_region),
                ('region-chip','regions','chip != 0x6034 ||','(chip != 0x6034 && false) ||'),
                ('region-preload','regions','u32_property(&adt, nub, b"pre-loaded")? != 1','(u32_property(&adt, nub, b"pre-loaded")? != 1 && false)'),
                ('region-names','regions','names != b"__TEXT;__DATA" && names != b"__TEXT;__DATA\\0"','names != b"__TEXT;__DATA" && names != b"__TEXT;__DATA\\0" && false'),
                ('region-nub','regions','required(&adt, nub, b"segment-ranges")? != ranges','(required(&adt, nub, b"segment-ranges")? != ranges && false)'),
                ('region-DRAM','regions','start < dram_start || end > dram_end','(start < dram_start || end > dram_end) && false'),
                ('region-alignment','regions','(start | size) & (hw::PAGE - 1) != 0','((start | size) & (hw::PAGE - 1) != 0 && false)'),
                ('region-PA-width','regions','end - 1 > (hw::PA_MASK | (hw::PAGE - 1))','(end - 1 > (hw::PA_MASK | (hw::PAGE - 1)) && false)'),
                ('region-physical-replica','regions','le64(row, 16) != Some(start)','(le64(row, 16) != Some(start) && false)'),
                ('region-flags','regions','le32(row, 28) != Some(if index == 0 { 1 } else { 0 })','(le32(row, 28) != Some(if index == 0 { 1 } else { 0 }) && false)'),
                ('region-caller-range','regions','expected.start != start || expected.end != end','(expected.start != start || expected.end != end) && false'),
                ('region-virtual','regions','le64(row, 8) != Some(va)','le64(row, 8) != Some(va) && false'),
                ('region-overlap','regions','text.start < data.end && data.start < text.end','text.start < data.end && data.start < text.end && false'),
            ]:
                attempt=run(label,(key,old,new));assert attempt.returncode==101 and b'panicked at' in attempt.stderr,(label,attempt.stdout,attempt.stderr)
                rejected.append(label)
        version=subprocess.run([args.rustc,'--version'],check=True,capture_output=True,text=True).stdout.strip()
        receipt={'rustc':version,'cases':result.stdout.decode().strip(),'region_cases':len(cases),'region_accepted':sum(row[-1] for row in cases),'mutants_rejected':rejected,'prior_identity_rejected':baseline_rejected,'prior_identity_failure_kind':baseline_failure_kind,'prior_identity_sha256':baseline_sha256,'sealed_token':'Caller construction rejected by compiler E0451',
                 'scope':'Actual complete firmware identity/policy/source helper, owned-ADT structural helpers, native segment parser/accessor, preflight/copy/lookup and source/device token; modeled NativeAdt owner construction, RAM/map/allocation/Arc/ARef/SHA/machine/parameter/log services. Invented metadata only; real image tuples remain untagged; no hardware permission, current-image or physical freshness proof.',
                 'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
        if args.receipt:args.receipt.write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps({k:v for k,v in receipt.items() if k!='source_sha256'},indent=2))


if __name__=='__main__':main()

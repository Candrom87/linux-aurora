#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Actual export-owner C metadata helper; modeled OF/PFN services only."""
import argparse,hashlib,json,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
def block(text,marker):
    a=text.index(marker);z=text.index('{',a)+1;depth=1
    while depth:
        depth+=(text[z]=='{')-(text[z]=='}');z+=1
    return text[a:z]
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cc',default='gcc');p.add_argument('--mutants',action='store_true')
    p.add_argument('--receipt',type=Path);a=p.parse_args()
    glue=ROOT/'drivers/gpu/drm/asahi/g15/glue.c'
    original=block(glue.read_text(),'int asahi_g15_native_adt_phram(u64 *start, u64 *size)\n{')
    fixture=(HERE/'fixture.c').read_text();rejected=[]
    with tempfile.TemporaryDirectory(prefix='g15-source-owner-') as temp:
        work=Path(temp)
        def run(label,code):
            source=work/(label+'.c');binary=work/label
            source.write_text(fixture.replace('/* OWNER_FUNCTION */',code))
            subprocess.run([a.cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-include',str(HERE.parent/'test-support/expected-failure.h'),str(source),'-o',str(binary)],capture_output=True,check=True)
            return subprocess.run([str(binary)],capture_output=True,timeout=30)
        result=run('actual',original);assert result.returncode==0,result.stdout+result.stderr
        if a.mutants:
            for label,old,new in [
                ('duplicate-owner','count != 1','(false && count != 1)'),
                ('disabled-owner','!of_device_is_available(owner)','(false && !of_device_is_available(owner))'),
                ('reusable-owner','of_property_read_bool(owner, "reusable")','(false && of_property_read_bool(owner, "reusable"))'),
                ('nomap-owner','of_property_read_bool(owner, "no-map")','(false && of_property_read_bool(owner, "no-map"))'),
                ('extra-range','of_get_address(owner, 1, NULL, NULL)','(false && of_get_address(owner, 1, NULL, NULL))'),
                ('size-limit','bytes > 0x200000','(false && bytes > 0x200000)'),
                ('alignment','(res.start | bytes) & 0x3fff','(false && ((res.start | bytes) & 0x3fff))'),
                ('ram-policy','!asahi_g15_ram_within(res.start, bytes)','(false && !asahi_g15_ram_within(res.start, bytes))'),
                ('owner-reference','of_node_put(owner);','if (false) of_node_put(owner);'),
            ]:
                assert original.count(old)==1,label
                attempt=run(label,original.replace(old,new,1))
                assert attempt.returncode!=0,(label,attempt.stdout,attempt.stderr)
                rejected.append(label)
    files=[glue,HERE/'fixture.c',Path(__file__).resolve(),HERE.parent/'test-support/expected-failure.h']
    receipt={'passed':True,'metadata_cases':1034,'summary':result.stdout.decode().strip(),'mutants_rejected':rejected,'scope':__doc__,'source_sha256':{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in files}}
    if a.receipt:a.receipt.write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:v for k,v in receipt.items() if k!='source_sha256'},indent=2))
if __name__=='__main__':main()

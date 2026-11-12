#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Actual immutable native property parser on invented bounded ADTs only."""
import argparse,copy,hashlib,json,struct,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
G15=ROOT/'drivers/gpu/drm/asahi/g15'
def block(text,marker):
 a=text.index(marker);z=text.index('{',a)+1;d=1
 while d:d+=(text[z]=='{')-(text[z]=='}');z+=1
 return text[a:z]
def node(name,props=(),children=()):return [list(props)+[('name',name.encode()+b'\0')],list(children)]
def encode(t):
 out=struct.pack('<II',len(t[0]),len(t[1]))
 for key,data in t[0]:out+=key.encode().ljust(32,b'\0')+struct.pack('<I',len(data))+data+bytes(-len(data)%4)
 return out+b''.join(encode(c) for c in t[1])
def tree(n=14,sram=True):
 main=b''.join(struct.pack('<II',24000000+r*53000000,500+r+lane) for lane in range(4) for r in range(n))
 aux=struct.pack('<QQ',1,2)+struct.pack('<QQQQQ',600000,24000000,700000,48000000,789000)
 props=[('compatible',b'gpu,t6034\0'),('gpu-num-perf-states',struct.pack('<I',n-1)),('perf-state-count',struct.pack('<I',n)),('perf-state-table-count',struct.pack('<I',4)),('perf-states',main+b'TAIL'),('cs-perf-states',aux),('afr-perf-states',aux)]
 if sram:props.append(('perf-states-sram',main+b'SRAM'))
 sensors=b''.join(b'RT'+bytes([48+i,k])+struct.pack('<II',i+1,0) for i,k in enumerate(b'ggggrf'))
 return node('root',children=[node('arm-io',children=[node('sgx',props),node('aon-ptd',[('ptd_data_table',sensors)])])])
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--rustc',default='rustc');p.add_argument('--mutants',action='store_true');p.add_argument('--receipt',type=Path);a=p.parse_args()
 rows=[]
 def add(t,want=False,chip=0x6034,n=14):
  length=len(next(v for k,v in t[1][0][1][0][0] if k=='perf-states')) if want else 0
  rows.append((encode(t) if isinstance(t,list) else t,want,chip,n,length))
 for n in range(2,15):
  for sram in [False,True]:add(tree(n,sram),True,n=n)
 base=tree();sgx=lambda t:t[1][0][1][0][0]
 for key in ['compatible','gpu-num-perf-states','perf-state-count','perf-state-table-count','perf-states','perf-states-sram','cs-perf-states','afr-perf-states']:
  original=next(v for k,v in sgx(base) if k==key)
  for value in [b'',original[:-1],original+b'X']:
   t=copy.deepcopy(base);sgx(t)[:]=[(k,value if k==key else v) for k,v in sgx(t)]
   # Import spans intentionally retain extension bytes; exact scalar/header shape
   # cases are distinct from allowed main/auxiliary property extension.
   valid=key in ['perf-states','perf-states-sram','cs-perf-states','afr-perf-states'] and len(value)>= (448 if key.startswith('perf-states') else 56)
   add(t,valid)
  t=copy.deepcopy(base);sgx(t).append((key,original));add(t)
 for key,values in [('gpu-num-perf-states',[0,14,0xffffffff]),('perf-state-count',[0,13,15]),('perf-state-table-count',[0,1,8,0xffffffff])]:
  for value in values:
   t=copy.deepcopy(base);sgx(t)[:]=[(k,struct.pack('<I',value) if k==key else v) for k,v in sgx(t)];add(t)
 for index in [0,1]:
  t=copy.deepcopy(base);parent=t if index==0 else t[1][0];parent[1].append(copy.deepcopy(parent[1][0]));add(t)
 t=copy.deepcopy(base);sgx(t).append(('name',b'sgx\0'));add(t)
 t=copy.deepcopy(base);sgx(t)[:]=[(k,b'sgx-extra\0' if k=='name' else v) for k,v in sgx(t)];add(t)
 for chip in [0,0x6031,0x6035]:add(base,chip=chip)
 raw=encode(base)
 for end in range(len(raw)):add(raw[:end])
 add(raw+bytes(0x200001-len(raw)))
 adt=(G15/'adt.rs').read_text();helpers='\n'.join(block(adt,m) for m in ["struct Adt<'a>",'fn le32(','fn le64(','fn strncmp_eq(',"impl Adt<'_>"])
 fixture='#![allow(dead_code,unused_imports)]\nuse std::io::Read;\nmod g15 { pub mod board {\n#[path="WORDS"]pub mod words;\n#[path="RECORD"]pub mod record;\n} pub mod adt {HELPERS\n#[path="PARSER"]pub mod native;\n} }\nfn main(){let mut input=Vec::new();std::io::stdin().read_to_end(&mut input).unwrap();let mut at=0;\nwhile at<input.len(){let cell=|n|u32::from_le_bytes(input[n..n+4].try_into().unwrap());let chip=cell(at);let want=cell(at+4)!=0;let size=cell(at+8)as usize;let n=cell(at+12);let length=cell(at+16)as usize;at+=20;let data=&input[at..at+size];at+=size;\nlet parsed=g15::adt::native::parse(data,chip);assert_eq!(parsed.is_ok(),want);\nif let Ok(p)=parsed{assert_eq!(p.table_count,4);assert_eq!(p.gpu_num_perf_states+1,n);for span in [Some(p.main),p.sram,Some(p.cs),Some(p.afr)].into_iter().flatten(){assert!(span.as_ptr()>=data.as_ptr());assert!((span.as_ptr()as usize+span.len())<=data.as_ptr()as usize+data.len());}assert_eq!(p.main.len(),length);}\n}println!("native property frames passed");}\n'
 stream=b''.join(struct.pack('<5I',chip,int(want),len(data),n,length)+data for data,want,chip,n,length in rows)
 native=(G15/'adt/native.rs').read_text();rejected=[]
 with tempfile.TemporaryDirectory(prefix='g15-native-properties-')as temp:
  w=Path(temp)
  def run(label,text):
   parser=w/(label+'-parser.rs');parser.write_text(text);src=w/(label+'.rs');binary=w/label
   s=fixture.replace('HELPERS',helpers).replace('WORDS',str(G15/'board/words.rs')).replace('RECORD',str(G15/'board/record.rs')).replace('PARSER',str(parser));src.write_text(s)
   subprocess.run([a.rustc,'--edition=2021','-O',src,'-o',binary],check=True,capture_output=True)
   return subprocess.run([binary],input=stream,capture_output=True,timeout=60)
  r=run('actual',native);assert r.returncode==0,r.stdout+r.stderr
  if a.mutants:
   for label,old,new in [('duplicate-property','return Err(\"native ADT duplicate property\");','let _ = \"native ADT duplicate property\";'),('chip','chip != 0x6034 ||','(false && chip != 0x6034) ||'),('main-tables','table_count != 4','(false && table_count != 4)'),('main-span','main.len() < need','(false && main.len() < need)'),('sram-span','sram.is_some_and(|value| value.len() < need)','(false && sram.is_some_and(|value| value.len() < need))')]:
    assert native.count(old)==1,(label,native.count(old));q=run(label,native.replace(old,new,1));assert q.returncode==101,(label,q.returncode);rejected.append(label)
 files=[G15/'adt.rs',G15/'adt/native.rs',G15/'board/record.rs',G15/'board/words.rs',Path(__file__).resolve()]
 receipt={'passed':True,'frames':len(rows),'accepted':sum(x[1]for x in rows),'mutants_rejected':rejected,'scope':__doc__,'source_sha256':{str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest()for f in files}}
 if a.receipt:a.receipt.write_text(json.dumps(receipt,indent=2)+'\n')
 print(json.dumps({k:v for k,v in receipt.items()if k!='source_sha256'},indent=2))
if __name__=='__main__':main()

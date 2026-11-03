#!/usr/bin/env python3
"""Exercise actual scatter admission and mapping code with CPU-only fake objects.

No kernel or hardware is used. Constants, layout declarations, validation
functions, planner and mapping loop are extracted from the checked-out sources.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
G15 = ROOT / 'drivers/gpu/drm/asahi/g15'


def block(text, marker):
    start = text.index(marker)
    opening = text.index('{', start)
    depth = 1
    for end in range(opening + 1, len(text)):
        depth += (text[end] == '{') - (text[end] == '}')
        if depth == 0:
            return text[start:end + 1]
    raise ValueError('unterminated source block: ' + marker)


FIXTURE = r'''
#![allow(dead_code)]
type Result<T=()> = std::result::Result<T,i32>;
const EINVAL:i32=-22;
#[path="PLANNER"] mod mmio_plan;
use mmio_plan::{Mapping,PhysRange};
mod hw {
    CONSTANTS
    #[derive(Clone,Copy,PartialEq)] pub enum Board {Templates,DeviceTree(()),None}
    DECLARATIONS
}
use hw::{va,IoMap,Layout,ScatterIoMap};
FIRMWARE_CONSTANTS
mod board {pub mod record {pub const IOMAP_SLOTS:usize=SLOT_COUNT;}}
struct Config {layout:&'static Layout,board:hw::Board,uat_oas:u32}
VALIDATION
#[derive(Default)] struct Objects {calls:Vec<(u64,u64,u64)>,fail:Option<usize>}
impl Objects {
    fn map_mmio(&mut self,va:u64,pa:u64,len:u64)->Result {
        self.calls.push((va,pa,len));
        if self.fail==Some(self.calls.len()) {Err(-5)} else {Ok(())}
    }
}
fn map(cfg:&Config,objects:&mut Objects)->Result {
    check_scatter(cfg)?;
    let layout=cfg.layout;
    MAPPING_LOOP
    Ok(())
}
fn config(maps:Vec<IoMap>,records:Vec<(u32,u32,bool)>,lists:Vec<ScatterIoMap>)->Config {
    Config {layout:Box::leak(Box::new(Layout {
        root_magic:0,hwdata_va:va::HWDATA,hwdata_size:0xb480,hwdata_cached:None,
        hwdata_view:0,iomaps:Box::leak(maps.into_boxed_slice()),
        scatter_iomaps:Box::leak(lists.into_boxed_slice()),
        record_slots:Box::leak(records.into_boxed_slice()),record_empty_writable:&[],
    })),board:hw::Board::DeviceTree(()),uat_oas:42}
}
fn fixture()->Config {
    config(vec![IoMap {slot:4,pa:0x200000,map_bytes:0x30000,va:hw::HOST+0x90000000,delta:0},
                IoMap {slot:7,pa:0x600000,map_bytes:0x4000,va:hw::HOST+0x91000000,delta:0}],
           vec![(0x30000,0x8000,true),(0x4000,0x4000,false)],
           vec![ScatterIoMap {slot:4,ranges:Box::leak((0..6).map(|n|PhysRange {
               start:0x200000+n*0x200000,len:0x8000}).collect::<Vec<_>>().into_boxed_slice())}])
}
fn alter(mut cfg:Config,f:impl FnOnce(&mut Layout))->Config {
    let old=cfg.layout;
    let mut layout=Layout {root_magic:old.root_magic,hwdata_va:old.hwdata_va,
        hwdata_size:old.hwdata_size,hwdata_cached:old.hwdata_cached,hwdata_view:old.hwdata_view,
        iomaps:old.iomaps,scatter_iomaps:old.scatter_iomaps,record_slots:old.record_slots,
        record_empty_writable:old.record_empty_writable};
    f(&mut layout);cfg.layout=Box::leak(Box::new(layout));cfg
}
fn change_map(cfg:Config,f:impl FnOnce(&mut Vec<IoMap>))->Config {
    alter(cfg,|l|{let mut v=l.iomaps.to_vec();f(&mut v);l.iomaps=Box::leak(v.into_boxed_slice());})
}
fn reject(cfg:Config) {
    let mut objects=Objects::default();
    assert_eq!(check_scatter(&cfg),Err(EINVAL));
    assert_eq!(map(&cfg,&mut objects),Err(EINVAL));assert!(objects.calls.is_empty());
}
#[test] fn actual_mapping_loop_packs_six_ranges_then_contiguous() {
    let cfg=fixture();let mut objects=Objects::default();map(&cfg,&mut objects).unwrap();
    let v=cfg.layout.iomaps[0].va;
    let expected:Vec<_>=(0..6).map(|n|(v+n*0x8000,0x200000+n*0x200000,0x8000))
        .chain([(cfg.layout.iomaps[1].va,0x600000,0x4000)]).collect();
    assert_eq!(objects.calls,expected);
}
#[test] fn mapping_error_stops_later_calls() {
    let cfg=fixture();let mut objects=Objects {fail:Some(3),..Objects::default()};
    assert_eq!(map(&cfg,&mut objects),Err(-5));assert_eq!(objects.calls.len(),3);
}
#[test] fn inactive_layout_retains_contiguous_sequence() {
    let cfg=alter(fixture(),|l|l.scatter_iomaps=&[]);
    let mut objects=Objects::default();map(&cfg,&mut objects).unwrap();
    assert_eq!(objects.calls,vec![(cfg.layout.iomaps[0].va,0x200000,0x30000),
                                (cfg.layout.iomaps[1].va,0x600000,0x4000)]);
}
#[test] fn rejects_geometry_and_record_mismatch_without_mapping() {
    reject(alter(fixture(),|l|l.record_slots=&[]));
    reject(alter(fixture(),|l|l.record_slots=&[(0x30000,0x4000,true),(0x4000,0x4000,false)]));
    reject(alter(fixture(),|l|l.record_slots=&[(0x30000,0x8000,true),(0x8000,0x4000,false)]));
    reject(change_map(fixture(),|m|m[0].delta=1));
    reject(change_map(fixture(),|m|m[0].map_bytes=0));
    reject(change_map(fixture(),|m|m[1].slot=m[0].slot));
    reject(change_map(fixture(),|m|m[0].slot=31));
    reject(alter(fixture(),|l|l.record_empty_writable=&[4]));
    reject(alter(fixture(),|l|l.record_empty_writable=&[3,3]));
    reject(alter(fixture(),|l|l.record_empty_writable=&[31]));
}
#[test] fn rejects_invalid_lists_without_mapping() {
    reject(alter(fixture(),|l|l.scatter_iomaps=&[ScatterIoMap {slot:9,ranges:&[]}]));
    reject(alter(fixture(),|l|l.scatter_iomaps=&[ScatterIoMap {slot:4,ranges:&[]} ]));
    reject(alter(fixture(),|l|l.scatter_iomaps=&[
        ScatterIoMap {slot:4,ranges:&[]},ScatterIoMap {slot:4,ranges:&[]} ]));
    for (start,len) in [(0x200000,0x8000),(1,0x8000),(1u64<<42,0x8000),(u64::MAX-0x3fff,0x4000)] {
        reject(alter(fixture(),|l|{
            let mut ranges=l.scatter_iomaps[0].ranges.to_vec();ranges[1]=PhysRange {start,len};
            l.scatter_iomaps=Box::leak(vec![ScatterIoMap {slot:4,
                ranges:Box::leak(ranges.into_boxed_slice())}].into_boxed_slice());
        }));
    }
    let mut cfg=fixture();cfg.board=hw::Board::Templates;reject(cfg);
    let mut cfg=fixture();cfg.uat_oas=64;reject(cfg);
    let mut cfg=fixture();cfg.uat_oas=43;reject(cfg);
}
#[test] fn rejects_reserved_windows_and_address_overflow() {
    for address in [va::ROOT,va::REGION_A,va::RUNTIME,va::HWDATA,va::PAIR,va::SMALL,
        va::C0,va::C1,va::C3,va::GLOBALS,va::POWER,va::CONTROL,va::FLAGS,
        va::DEVCTL_STATE,va::DEVCTL_RING,va::EVENT_STATE,va::EVENT_RING,
        va::LOG_STATE,va::LOG_RING,va::TRACE_STATE,va::TRACE_RING,
        va::STATS_STATE,va::STATS_RING,va::FWCTL_STATE,va::FWCTL_RING,
        va::SELF_CHECK+4*hw::PAGE,va::FW_ALLOC.start,va::TIMESTAMPS,
        hw::HOST-hw::PAGE,hw::HOST+hw::HOST_SIZE, u64::MAX-0x3fff] {
        reject(change_map(fixture(),|m|m[0].va=address & !(hw::PAGE-1)));
    }
    for n in 0..hw::rings::CHANNELS {
        for address in [va::ch_state(n),va::ch_ring(n)] {
            reject(change_map(fixture(),|m|m[0].va=address & !(hw::PAGE-1)));
        }
    }
    reject(change_map(fixture(),|m|m[1].va=m[0].va+hw::PAGE));
    reject(change_map(fixture(),|m|m[1].pa=(1u64<<42)-hw::PAGE+m[1].map_bytes));
}
'''


def main():
    hw = (G15 / 'hw.rs').read_text()
    source = (G15 / 'initdata.rs').read_text()
    build = block(source, 'pub(crate) fn build(')
    prepare = block(source, 'pub(crate) fn prepare_board(')
    admit = block((G15 / 'boot.rs').read_text(), 'pub(crate) fn admit(')
    assert prepare.index('check_scatter(cfg)?;') < prepare.index('match cfg.board')
    assert build.index('check_scatter(cfg)?;') < build.index('canonical(objects,')
    assert admit.index('initdata::prepare_board(') < admit.index('regs::Regs::new(') < admit.index('glue::hold_power(')
    # Existing profiles must stay inactive; no board constants are imported into the fixture.
    for profile in [block(hw, 'pub(crate) mod t6030'), (G15 / 'hw/t6031.rs').read_text()]:
        assert 'scatter_iomaps: &[],' in profile
    obj = block((G15 / 'object.rs').read_text(), 'pub(crate) fn self_check(')
    window = re.search(r'let window = \[(.*?)\];', obj, re.S).group(1)
    assert len(re.findall(r'pte::\w+', window)) == 5
    consts = '\n'.join(re.search(r'pub\(crate\) const '+name+r':[^;]+;', hw).group()
                       for name in ('PAGE', 'PA_MASK', 'HOST', 'HOST_SIZE'))
    consts += '\n' + block(hw, 'pub(crate) mod va') + '\n' + block(hw, 'pub(crate) mod rings')
    decls = '\n'.join(block(hw, 'pub(crate) struct '+name) for name in ('Layout','IoMap','ScatterIoMap'))
    decls = decls.replace('pub(crate) struct IoMap', '#[derive(Clone,Copy)] pub(crate) struct IoMap')
    fw = (G15 / 'fw/initdata.rs').read_text()
    modules = ('root','region_a','runtime','pair','small','cbuf','globals','power','control','flags')
    validation = source[source.index('/// Whether a page-rounded fixed object'):source.index('/// Build and check the board images')]
    loop = block(build, 'for map in layout.iomaps.iter()')
    text = FIXTURE.replace('PLANNER',str(G15 / 'mmio_plan.rs')).replace('    CONSTANTS\n',consts+'\n')
    text = text.replace('DECLARATIONS',decls).replace('FIRMWARE_CONSTANTS','\n'.join(block(fw,'pub(crate) mod '+m) for m in modules))
    slots = re.search(r'pub\(crate\) const IOMAP_SLOTS: usize = (\d+);',(G15/'board/record.rs').read_text()).group(1)
    text = text.replace('SLOT_COUNT',slots).replace('VALIDATION',validation).replace('MAPPING_LOOP',loop)
    with tempfile.TemporaryDirectory(prefix='asahi-scatter-test-') as directory:
        src=Path(directory)/'test.rs';exe=Path(directory)/'test';src.write_text(text)
        subprocess.run([os.environ.get('RUSTC','rustc'),'--edition=2021','--test',str(src),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    print('Actual-source planner, validation and map sequence passed; no hardware accessed')


if __name__ == '__main__':
    main()

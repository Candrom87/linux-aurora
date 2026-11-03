#!/usr/bin/env python3
"""Execute production DART flush and consumer-aperture paths on fake devices."""
from pathlib import Path
import argparse
import subprocess
import tempfile
import re
import os


def consumer_windows(text):
    helper = (function(text, 'apple_dart_validate_dma_window')
              if 'static int apple_dart_validate_dma_window(' in text else '')
    production = helper + '\n' + function(text, 'apple_dart_parse_dma_window')
    production += '\n' + function(text, 'apple_dart_of_xlate')
    code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <stdio.h>
typedef uint64_t u64;
typedef uint64_t dma_addr_t;
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define IS_ALIGNED(x,a) (!((x)&((a)-1)))
#define DMA_BIT_MASK(n) ((1ULL<<(n))-1)
#define check_add_overflow(a,b,p) __builtin_add_overflow((a),(b),(p))
#define dev_err(...) ((void)0)
#define MAX_DARTS_PER_DEVICE 2
#define kzalloc_obj(x) calloc(1,sizeof(x))
#define max(a,b) ((a)>(b)?(a):(b))
#define min(a,b) ((a)<(b)?(a):(b))
#define set_bit(n,p) (*(p)|=1UL<<(n))
struct platform_device;
struct device_node { bool present; int length; u64 value[2]; struct platform_device *provider; };
struct device { struct device_node *of_node; void *priv; };
struct apple_dart { u64 dma_min,dma_max; unsigned ias,pgsize; bool supports_bypass,locked; };
struct platform_device { struct device dev; struct apple_dart *dart; };
struct of_phandle_args { unsigned args_count,args[8]; struct device_node *np; };
struct apple_dart_stream_map { struct apple_dart *dart; unsigned long sidmap[1]; };
struct apple_dart_master_cfg {
 bool supports_bypass,locked; u64 dma_min,dma_max;
 struct apple_dart_stream_map stream_maps[MAX_DARTS_PER_DEVICE];
};
static bool of_property_present(struct device_node *n,const char *key){return n && n->present;}
static int of_property_read_u64_array(struct device_node *n,const char *key,u64 *out,unsigned count){
 assert(count==2);if(!n || n->length!=16)return -EINVAL;memcpy(out,n->value,16);return 0;
}
static void *dev_iommu_priv_get(struct device *d){return d->priv;}
static void dev_iommu_priv_set(struct device *d,void *p){d->priv=p;}
static struct platform_device *of_find_device_by_node(struct device_node *n){return n->provider;}
static struct apple_dart *platform_get_drvdata(struct platform_device *p){return p->dart;}
static void put_device(struct device *d){}
'''+production+r'''
static void args_window(struct of_phandle_args *a,u64 base,u64 size){
 a->args_count=5;a->args[1]=base>>32;a->args[2]=base;
 a->args[3]=size>>32;a->args[4]=size;
}
int main(void){
 const u64 alias=1ULL<<40,size=1ULL<<36,page=1ULL<<14;
 struct device_node consumer={.present=true,.length=16,.value={alias,size}};
 struct apple_dart high={.dma_min=alias,.dma_max=alias+size-1,.ias=36,.pgsize=1<<14};
 struct apple_dart low={.dma_min=0,.dma_max=size-(1<<16)-1,.ias=36,.pgsize=1<<14};
 struct platform_device ph={.dart=&high},pl={.dart=&low};
 struct device_node nh={.provider=&ph},nl={.provider=&pl};
 struct of_phandle_args a={.args_count=1,.np=&nh};
 struct device d={.of_node=&consumer};
 assert(!apple_dart_of_xlate(&d,&a));a.np=&nl;a.args[0]=1;
 assert(!apple_dart_of_xlate(&d,&a));
 struct apple_dart_master_cfg *cfg=d.priv;
 assert(cfg && cfg->dma_min==alias && cfg->dma_max==alias+size-1);
 assert(cfg->stream_maps[0].dart==&high && cfg->stream_maps[1].dart==&low);
 free(d.priv);d.priv=NULL;
 // Independent consumer on the low provider keeps its original window.
 struct device separate={0};a.args[0]=4;
 assert(!apple_dart_of_xlate(&separate,&a));cfg=separate.priv;
 assert(cfg->dma_min==low.dma_min && cfg->dma_max==low.dma_max);
 free(separate.priv);
 u64 first,last;
 args_window(&a,0,size-(1<<16));
 assert(!apple_dart_parse_dma_window(&d,&low,&a,&first,&last));
 assert(first==alias && last==alias+size-1);
 d.of_node=NULL;
 assert(!apple_dart_parse_dma_window(&d,&low,&a,&first,&last));
 assert(first==0 && last==size-(1<<16)-1);
 d.of_node=&consumer;
 const unsigned bad_counts[]={0,2,4,6};
 for(unsigned i=0;i<ARRAY_SIZE(bad_counts);i++){
  a.args_count=bad_counts[i];assert(apple_dart_of_xlate(&d,&a)==-EINVAL && !d.priv);
 }
 const u64 bad[][2]={{0,0},{1,page},{0,page+1},{UINT64_MAX-page+1,2*page},{0,1ULL<<37}};
 for(unsigned i=0;i<ARRAY_SIZE(bad);i++){
  // A valid consumer override must not hide invalid explicit stream cells.
  args_window(&a,bad[i][0],bad[i][1]);
  assert(apple_dart_of_xlate(&d,&a)==-EINVAL && !d.priv);
  // Nor may a valid stream hide an invalid consumer aperture.
  args_window(&a,0,page);consumer.value[0]=bad[i][0];consumer.value[1]=bad[i][1];
  assert(apple_dart_of_xlate(&d,&a)==-EINVAL && !d.priv);
  consumer.value[0]=alias;consumer.value[1]=size;
 }
 a.args_count=1;consumer.length=4;
 assert(apple_dart_of_xlate(&d,&a)==-EINVAL && !d.priv);
 consumer.length=16;
 // Without an override disjoint provider defaults must still be refused.
 d.of_node=NULL;a.np=&nh;a.args[0]=0;
 assert(!apple_dart_of_xlate(&d,&a));a.np=&nl;
 assert(apple_dart_of_xlate(&d,&a)==-EINVAL);
 cfg=d.priv;assert(cfg->dma_min==alias && cfg->dma_max==alias+size-1);free(d.priv);
 puts("PASS: production parser/of_xlate consumer alias, independent defaults, five-cell precedence and malformed controls");
}
'''
    with tempfile.TemporaryDirectory(prefix='dart-consumer-window-') as directory:
        p=Path(directory);source=p/'controls.c';binary=p/'controls'
        source.write_text(code)
        subprocess.run(['cc','-std=gnu11','-Werror','-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer',str(source),'-o',str(binary)],check=True)
        subprocess.run([str(binary)],env=dict(os.environ,ASAN_OPTIONS='detect_leaks=0'),check=True)


def function(text, name):
    match = re.search(r'^static int ' + re.escape(name) + r'\(', text, re.M)
    start = text.index('{', match.start())
    depth = 1
    end = start + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[match.start():end]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path,
                        default=Path(__file__).resolve().parents[2] / 'drivers/iommu/apple-dart.c')
    parser.add_argument('--consumer-only', action='store_true')
    args = parser.parse_args()
    if args.consumer_only:
        consumer_windows(args.source.read_text())
        return
    production = function(args.source.read_text(), 'apple_dart_domain_flush_tlb_range')
    source = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
typedef uint64_t u64;
typedef uint32_t u32;
#define BITS_TO_LONGS(n) 1
#define atomic_long_read(x) (*(x))
#define bitmap_empty(x, n) (!(x)[0])
#define dev_err_ratelimited(...) ((void)0)
#define DART_T8110_TLB_CMD_OP_FLUSH_SID 1
struct io_pgtable_cfg { int unused; };
struct io_pgtable { struct io_pgtable_cfg cfg; };
#define io_pgtable_ops_to_pgtable(x) ((struct io_pgtable *)(x))
struct apple_dart;
struct apple_dart_stream_map { struct apple_dart *dart; unsigned long sidmap[1]; };
struct apple_dart_hw { int (*invalidate_tlb)(struct apple_dart_stream_map *); };
struct apple_dart { int num_streams, locked, version; bool fw_mirror, fw_handoff; void *dev; struct apple_dart_hw *hw; };
struct apple_dart_atomic_stream_map { struct apple_dart *dart; unsigned long sidmap[1]; };
struct apple_dart_domain {
 void *pgtbl_ops; struct apple_dart_atomic_stream_map streams[2]; int count;
 u64 dma_offset, mask;
};
#define for_each_stream_map(i,d,m) for ((i)=0; (i)<(d)->count && ((m)=&(d)->streams[i],1); (i)++)
static int powered, power_error, sync_error, range_error, syncs, ranges, fulls, firmware;
static u64 seen_first, seen_last;
static int pm_runtime_resume_and_get(void *dev) { if (power_error) return power_error; powered++; return 0; }
static void pm_runtime_put(void *dev) { assert(powered>0); powered--; }
static int apple_dart_hw_sync_locked(struct io_pgtable_cfg *cfg, struct apple_dart_stream_map *stream, bool unmap) {
 assert(unmap); syncs++; return sync_error;
}
static int apple_dart_fw_invalidate(struct apple_dart_stream_map *stream, u64 first, u64 last) {
 firmware++; seen_first=first; seen_last=last; return range_error;
}
static int apple_dart_t8110_hw_tlb_command_range(struct apple_dart_stream_map *stream,
 int command, bool range, u64 first, u64 last) {
 assert(command==1 && range); ranges++; seen_first=first; seen_last=last; return range_error;
}
static int full(struct apple_dart_stream_map *stream) { fulls++; return 0; }
'''
    source += production
    source += r'''
static void reset(void) {
 assert(powered==0); power_error=sync_error=range_error=syncs=ranges=fulls=firmware=0;
 seen_first=seen_last=0;
}
int main(void) {
 struct apple_dart_hw hw={.invalidate_tlb=full};
 struct apple_dart dart={.num_streams=8,.locked=1,.version=0x202,.hw=&hw};
 struct io_pgtable pg={0};
 struct apple_dart_domain domain={.pgtbl_ops=&pg,.count=1,.dma_offset=1ULL<<40,.mask=(1ULL<<36)-1};
 domain.streams[0].dart=&dart; domain.streams[0].sidmap[0]=1;
 assert(!apple_dart_domain_flush_tlb_range(&domain,true,0x4000,0x7fff));
 assert(ranges==1 && fulls==0 && syncs==1 && powered==0);
 assert(seen_first==(1ULL<<40)+0x4000 && seen_last==(1ULL<<40)+0x7fff);
 reset(); domain.dma_offset=0;
 assert(!apple_dart_domain_flush_tlb_range(&domain,true,0x4000,0x7fff));
 assert(seen_first==0x4000 && seen_last==0x7fff);
 reset(); dart.version=0x201;
 assert(!apple_dart_domain_flush_tlb_range(&domain,true,0x4000,0x7fff));
 assert(fulls==1 && ranges==0);
 reset(); dart.version=0x202; dart.locked=0;
 assert(!apple_dart_domain_flush_tlb_range(&domain,true,0x4000,0x7fff));
 assert(fulls==1 && ranges==0 && syncs==0);
 reset(); dart.locked=1; sync_error=-EBUSY;
 assert(apple_dart_domain_flush_tlb_range(&domain,true,0x4000,0x7fff)==-EBUSY);
 assert(ranges==1 && fulls==0 && powered==0);
 reset(); power_error=-EHOSTDOWN;
 assert(apple_dart_domain_flush_tlb_range(&domain,true,0x4000,0x7fff)==-EHOSTDOWN);
 assert(syncs==0 && ranges==0 && fulls==0 && powered==0);
 reset(); range_error=-ETIMEDOUT;
 assert(apple_dart_domain_flush_tlb_range(&domain,true,0x4000,0x7fff)==-ETIMEDOUT);
 assert(ranges==1 && powered==0);
 reset(); dart.fw_mirror=true;
 assert(!apple_dart_domain_flush_tlb_range(&domain,true,0x4000,0x7fff));
 assert(firmware==1 && ranges==0 && powered==0);
 reset(); dart.fw_mirror=false; dart.fw_handoff=true;
 assert(!apple_dart_domain_flush_tlb_range(&domain,true,0x4000,0x7fff));
 assert(firmware==1 && ranges==0 && powered==0);
 dart.fw_handoff=false;
 reset(); domain.streams[0].sidmap[0]=0;
 assert(!apple_dart_domain_flush_tlb_range(&domain,true,0x4000,0x7fff));
 assert(syncs==0 && ranges==0 && fulls==0 && powered==0);
 puts("PASS: device DVA, low window, legacy/unlocked/firmware routing, sync/power/command errors and empty stream");
}
'''
    with tempfile.TemporaryDirectory(prefix='dart-range-') as directory:
        p = Path(directory)
        (p / 'controls.c').write_text(source)
        subprocess.run(['cc', '-std=gnu11', '-Werror', str(p / 'controls.c'), '-o', str(p / 'controls')], check=True)
        subprocess.run([str(p / 'controls')], check=True)
    consumer_windows(args.source.read_text())


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Exercise V14 framebuffer geometry using synthetic device-tree values."""
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'drivers/gpu/drm/apple/iomfb_v14_7.c'
BOARD = ROOT / 'drivers/gpu/drm/apple/iomfb_v14_7_board.h'


def block(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    depth = 0
    for i in range(opening, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if not depth:
                return text[start:i + 1]
    raise ValueError('unterminated function')


source = SOURCE.read_text()
board = BOARD.read_text()
helper = (block(board, 'static inline bool dcp_v14_panel_height(')
          if 'static inline bool dcp_v14_panel_height(' in board else '')
geometry = block(source, 'static int dcp_v14_geometry(')
program = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
#define MAX_NOTCH_HEIGHT 160
#define check_add_overflow(a,b,out) __builtin_add_overflow(a,b,out)
#define check_mul_overflow(a,b,out) __builtin_mul_overflow(a,b,out)
#define dev_err(...) do {} while (0)
struct dcp_v14_board {u32 panel_width, panel_height; const char *name;};
struct device_node {int id;};
struct device {struct device_node *of_node;};
struct apple_dcp_v14 {
    struct device *dev; struct dcp_v14_board *board;
    u32 fb_width,fb_height,stride,notch_rows,panel_width,panel_height;
};
static struct device_node fb={1}, panel={2};
static bool have_fb=true;
static const char *missing;
static u32 width,height,stride,notch;
static unsigned put_count;
static struct device_node *of_find_compatible_node(void *a,void *b,const char *name) {
    assert(!a && !b && !strcmp(name,"simple-framebuffer")); return have_fb ? &fb : NULL;
}
static int of_property_read_u32(struct device_node *n,const char *name,u32 *out) {
    assert(n==&fb || n==&panel);
    if (missing && !strcmp(name,missing)) return -EINVAL;
    if (!strcmp(name,"width")) *out=width;
    else if (!strcmp(name,"height")) *out=height;
    else if (!strcmp(name,"stride")) *out=stride;
    else if (!strcmp(name,"apple,notch-height")) *out=notch;
    else assert(0);
    return 0;
}
static void of_node_put(struct device_node *n) {assert(n==&fb); put_count++;}
HELPER
GEOMETRY
int main(void) {
    struct dcp_v14_board b={.panel_width=800,.panel_height=1000,.name="synthetic"};
    struct device d={.of_node=&panel};
    struct apple_dcp_v14 v={.dev=&d,.board=&b};
    width=800;stride=3200;height=900;notch=100;
    assert(dcp_v14_geometry(&v)==0 && v.panel_height==1000 && v.notch_rows==100);
    height=1000;
    assert(dcp_v14_geometry(&v)==0 && v.panel_height==1000);
    height=899;
    assert(dcp_v14_geometry(&v)==-EINVAL);
    b.panel_width=0;b.panel_height=0;
    width=0x40000001U;stride=4;height=1;notch=0;
    assert(dcp_v14_geometry(&v)==-EINVAL);
    width=0x40000000U;stride=0;
    assert(dcp_v14_geometry(&v)==-EINVAL);
    width=8;stride=28;
    assert(dcp_v14_geometry(&v)==-EINVAL);
    width=8;stride=32;height=UINT32_MAX;notch=2;
    assert(dcp_v14_geometry(&v)==-EINVAL);
    height=UINT32_MAX;notch=1;
    assert(dcp_v14_geometry(&v)==-EINVAL);
    width=8;stride=32;height=1;notch=MAX_NOTCH_HEIGHT+1;
    assert(dcp_v14_geometry(&v)==-EINVAL);
    notch=0;missing="stride";put_count=0;
    assert(dcp_v14_geometry(&v)==-EINVAL && put_count==1);
    missing=NULL;have_fb=false;put_count=0;
    assert(dcp_v14_geometry(&v)==-ENODEV && put_count==0);
    puts("Synthetic framebuffer geometry: crop, full height, bad arithmetic and property errors pass");
}
'''.replace('HELPER', helper).replace('GEOMETRY', geometry)

with tempfile.TemporaryDirectory(prefix='dcp-v14-geometry-') as tmp:
    path = Path(tmp)
    (path / 'test.c').write_text(program)
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                    '-O2', str(path / 'test.c'), '-o', str(path / 'test')], check=True)
    subprocess.run([str(path / 'test')], check=True)
print('Synthetic device-tree inputs only; no display or firmware access.')

#!/usr/bin/env python3
"""Compile actual swap-start/power declarations for each firmware layout.

Reads source files and uses only a host C compiler. No driver or device is run.
An optional old header must fail the 14.7 layout assertions as a negative control.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile


def declarations(header):
    text = header.read_text()
    start = text.index('/*\n * A406 swap_start')
    end = text.index('/*\n * D201 map_piodma', start)
    return text[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('apple', type=Path, help='drivers/gpu/drm/apple source directory')
    p.add_argument('--before', type=Path, help='old iomfb_template.h expected to fail for 14.7')
    p.add_argument('--cc', default='cc')
    args = p.parse_args()
    version = (args.apple / 'version_utils.h').read_text()
    macros = '\n'.join(re.search(pattern, version).group(0) for pattern in (
        r'#define DCP_FW_VERSION\([^\n]+',
        r'#define DCP_FW_LAYOUT_14_7\(ver\) \\\n[^\n]+',
    ))
    iomfb = (args.apple / 'iomfb.h').read_text()
    start = iomfb.index('struct dcp_iouserclient {')
    client = iomfb[start:iomfb.index('} __packed;', start) + len('} __packed;')]
    prefix = ('#include <stdint.h>\n#include <stddef.h>\n'
              'typedef uint64_t u64; typedef uint32_t u32; typedef uint8_t u8;\n'
              '#define __packed __attribute__((packed))\n'
              '#define static_assert _Static_assert\n#define DCP_FW_NAME(name) name\n' +
              macros + '\n' + client + '\n')
    cases = ((13, 5, 0), (14, 6, 0), (14, 7, 0), (14, 8, 3), (26, 0, 0), (26, 6, 0))
    with tempfile.TemporaryDirectory(prefix='dcp-swap-layout-') as tmp:
        tmp = Path(tmp)
        for release in cases:
            packed = release[0] >= 26 or release >= (14, 7, 0)
            req, resp, ret = (16, 8, 4) if packed else (24, 24, 20)
            power = 16 if release[0] >= 26 else 12
            checks = ('\n_Static_assert(sizeof(struct dcp_swap_start_req) == REQ, "request");\n'
                      '_Static_assert(sizeof(struct dcp_swap_start_resp) == RESP, "response");\n'
                      '_Static_assert(offsetof(struct dcp_swap_start_resp, ret) == RET, "status");\n'
                      '_Static_assert(sizeof(struct dcp_set_power_state_req) == POWER, "power");\n')
            checks = checks.replace('REQ', str(req)).replace('RESP', str(resp)).replace(
                'RET', str(ret)).replace('POWER', str(power))
            define = '#define DCP_FW_VER DCP_FW_VERSION(%d,%d,%d)\n' % release
            path = tmp / 'layout.c'
            path.write_text(prefix + define + declarations(args.apple / 'iomfb_template.h') + checks)
            subprocess.run([args.cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-fsyntax-only',
                            str(path)], check=True)
        if args.before:
            path.write_text(prefix + '#define DCP_FW_VER DCP_FW_VERSION(14,7,0)\n' +
                            declarations(args.before) +
                            '\n_Static_assert(sizeof(struct dcp_swap_start_resp) == 8, "regression");\n')
            result = subprocess.run([args.cc, '-std=c11', '-fsyntax-only', str(path)],
                                    capture_output=True, text=True)
            if result.returncode == 0 or 'regression' not in result.stderr:
                raise AssertionError('old header did not fail the regression assertion')
            print('Old 14.7 declaration failed the regression assertion as expected')
    print('Six source-extracted swap-start/power layout cases passed; no hardware accessed')


if __name__ == '__main__':
    main()

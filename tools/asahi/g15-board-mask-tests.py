#!/usr/bin/env python3
"""Run pure board tests and compare complete synthetic J516C records to old source.

Only file-backed source is compiled; no driver or hardware is used. The two
reference records use public J516C constants with synthetic operating points.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


EXPORT = r'''
    #[test]
    fn export_compatibility_records() {
        let main = main_opps();
        let cs = [Opp { mhz: 24, mv: [635, 0, 0, 0], cells: 1 },
                  Opp { mhz: 480, mv: [700, 0, 0, 0], cells: 1 }];
        let mut output = Vec::new();
        for count in [1, 2] {
            let mut input = inputs(&main, &cs[..count], &[]);
            input.reference = &J516C;
            let mut record = vec![0u8; SIZE];
            build(&mut record, &input).unwrap();
            output.extend(record);
        }
        std::fs::write(std::env::var_os("G15_MASK_COMPARE_OUTPUT").unwrap(), output).unwrap();
    }
'''


def compile_and_run(board, record, rustc, directory):
    directory.mkdir()
    for name in ['template', 'data', 'words']:
        shutil.copyfile(board / (name + '.rs'), directory / (name + '.rs'))
    source = record.read_text()
    end = source.rfind('\n}')
    if end < 0:
        raise ValueError('missing test module end')
    (directory / 'record.rs').write_text(source[:end] + EXPORT + source[end:])
    (directory / 'harness.rs').write_text('#![allow(dead_code)]\nmod board {\n' +
        '\n'.join('#[path = ' + json.dumps(str(directory / (n + '.rs'))) +
                  '] pub mod ' + n + ';' for n in ['template', 'data', 'words', 'record']) +
        '\npub(crate) use template::{Line, Template};\n}\n')
    binary = directory / 'checks'
    subprocess.run([rustc, '--edition=2021', '--test', str(directory / 'harness.rs'),
                    '-o', str(binary)], check=True)
    env = dict(os.environ, G15_MASK_COMPARE_OUTPUT=str(directory / 'records'))
    subprocess.run([str(binary)], env=env, check=True)
    return (directory / 'records').read_bytes()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('board', type=Path, help='current G15 board source directory')
    p.add_argument('--before', required=True, type=Path, help='pre-change record.rs')
    p.add_argument('--rustc', default='rustc')
    args = p.parse_args()
    with tempfile.TemporaryDirectory(prefix='g15-mask-compatibility-') as tmp:
        tmp = Path(tmp)
        before = compile_and_run(args.board, args.before, args.rustc, tmp / 'before')
        after = compile_and_run(args.board, args.board / 'record.rs', args.rustc, tmp / 'after')
        if len(before) != 2 * 0xb480 or before != after:
            raise AssertionError('synthetic J516C record bytes changed')
    print('Two complete synthetic J516C records unchanged; no hardware accessed')


if __name__ == '__main__':
    main()

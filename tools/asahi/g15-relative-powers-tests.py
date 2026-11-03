#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run actual core-only record tests, ratio mutants and optional before/after identity.

No hardware or private board calibration is read. The J516C identity fixture
uses existing Reference constants with synthetic OPPs/properties/sensor rows.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
EXTRA = r'''
    #[test]
    fn scoped_j516c_snapshot() {
        let main = main_opps();
        let cs = [Opp { mhz: 24, mv: [625,0,0,0], cells: 1 }];
        let mut input = inputs(&main, &cs, &[]);
        input.reference = &J516C;
        let mut out = vec![0; SIZE];
        build(&mut out, &input).unwrap();
        std::fs::write(std::env::var("G15_GOLDEN_OUTPUT").unwrap(), &out).unwrap();
    }
'''
MUTANTS = {
    'shift-relative-slots': ('put32(out, RELATIVE_POWER + 4 * state, value)?;',
                            'put32(out, RELATIVE_POWER + 4 * state + 4, value)?;'),
    'force-top100': ('put32(out, RELATIVE_POWER + 4 * state, value)?;',
                    'put32(out, RELATIVE_POWER + 4 * state, if state + 1 == selected_count { 100 } else { value })?;'),
    'ignore-selected-count': ('if self.count != selected_count {',
                              'if self.count != selected_count && false {'),
    'omit-state-zero': ('powers.for_states(selected_count)?.iter().enumerate()',
                        'powers.for_states(selected_count)?.iter().enumerate().skip(1)'),
    'reject-sixteen': ('if N == 0 || N > STATES {', 'if N == 0 || N >= STATES {'),
}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, default=ROOT / 'drivers/gpu/drm/asahi/g15/board/record.rs')
    p.add_argument('--before', type=Path, help='optional saved pre-edit record.rs for exact identity')
    p.add_argument('--board', type=Path, help='directory containing unchanged template/data/words modules')
    p.add_argument('--rustc', default='rustc')
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    board = (args.board or args.source.parent).resolve(strict=True)
    args.out.mkdir(parents=True, exist_ok=False)
    out = args.out.resolve()
    source = args.source.read_text()
    def run(label, text, expect_failure=False):
        at = text.rfind('\n}')
        if at < 0:
            raise ValueError('record test module terminator missing')
        file = out / (label + '.rs')
        file.write_text(text[:at] + EXTRA + text[at:])
        harness = out / (label + '-harness.rs')
        harness.write_text('#![allow(dead_code)]\nmod board {\n' + ''.join(
            '#[path="' + str(board / (name + '.rs')) + '"] pub mod ' + name + ';\n'
            for name in ('template', 'data', 'words')) + '#[path="' + str(file) + '"] pub mod record;\n'
            'pub(crate) use template::{Line,Template};\n}\n')
        binary = out / (label + '-tests')
        compile_argv = [args.rustc, '--edition=2021', '--test', str(harness), '-o', str(binary)]
        compilation = subprocess.run(compile_argv, text=True, capture_output=True, timeout=60)
        (out / (label + '-compile.log')).write_text(compilation.stdout + compilation.stderr)
        if compilation.returncode:
            raise RuntimeError('host compilation failed: ' + label)
        environment = dict(os.environ, G15_GOLDEN_OUTPUT=str(out / (label + '.record')))
        result = subprocess.run([str(binary)], env=environment, text=True, capture_output=True, timeout=60)
        (out / (label + '-tests.log')).write_text(result.stdout + result.stderr)
        if (result.returncode != 0) != expect_failure:
            raise RuntimeError('unexpected regression outcome: ' + label)
        return {'compile': compile_argv, 'exit': result.returncode, 'expected_failure': expect_failure,
                'summary': next((s for s in result.stdout.splitlines() if s.startswith('test result:')), '')}
    results = {'current': run('current', source)}
    for name, (old, new) in MUTANTS.items():
        if source.count(old) != 1:
            raise ValueError('production mutation anchor missing: ' + name)
        results[name] = run(name, source.replace(old, new), True)
    identity = None
    if args.before:
        results['before'] = run('before', args.before.read_text())
        before, after = (out / 'before.record').read_bytes(), (out / 'current.record').read_bytes()
        if before != after or len(after) != 0xb480:
            raise RuntimeError('J516C complete record bytes changed')
        identity = {'bytes': len(after), 'sha256': hashlib.sha256(after).hexdigest(), 'equal': True}
    receipt = {'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
               'module_sha256': {n: hashlib.sha256((board / (n + '.rs')).read_bytes()).hexdigest()
                                 for n in ('template', 'data', 'words')},
               'before_sha256': hashlib.sha256(args.before.read_bytes()).hexdigest() if args.before else None,
               'results': results, 'j516c_identity': identity,
               'limits': 'Actual core-only production record/word modules with synthetic data. Two off-tree measured J516C tests remain ignored. No live board profile or hardware access.'}
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'current': results['current']['summary'], 'mutants_rejected': len(MUTANTS),
                      'j516c_identity': identity}, indent=2))


if __name__ == '__main__':
    main()

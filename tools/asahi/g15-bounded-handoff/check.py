#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only OR MIT
"""Run actual PPL/timeout/retention source with synthetic host transports, never hardware."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
MMU = ROOT / 'drivers/gpu/drm/asahi/mmu.rs'
PPL = MMU.parent / 'mmu/ppl.rs'


def item(source, marker):
    start = source.index(marker)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def generate(mmu, ppl):
    transport = '\n'.join(item(mmu, x) for x in [
        "struct BoundedTransport<'a>", "impl ppl::Memory for BoundedTransport<'_>",
        "enum HandoffGuard<'a>", "impl HandoffGuard<'_>", "impl Drop for HandoffGuard<'_>",
    ])
    inner = 'impl UatInner {\n' + '\n'.join(item(mmu, x) for x in [
        'fn check_handoff(&self)', 'fn lock_handoff(&self)', 'fn lock_bounded_upper(&self',
    ]) + '\n}'
    vm = 'impl VmInner {\n' + '\n'.join(item(mmu, x) for x in [
        'fn unmap_gpuva(&mut self', 'fn rollback_failed_map(&mut self',
    ]) + '\n}'
    source = HERE.joinpath('fixture.rs').read_text()
    return source.replace('// @PRODUCTION@',
        'mod ppl {\n' + ppl + '\n}\n' + transport + inner + vm
        + item(mmu, 'impl Drop for KernelMapping')).replace('// @BACKEND@', item((MMU.parent/'g15/engine.rs').read_text(), 'pub(crate) fn check_uat('))


def audit():
    s = MMU.read_text()
    # Bounded transport must not select the distinct M4 user-upper/incarnation contract.
    assert 'm4_adopted' not in item(s, 'fn lock_handoff(&self)')
    assert 'm4_adopted' not in item(s, 'fn lock_bounded_upper(&self')
    for name in ['sm_step_map', 'sm_step_remap', 'unmap_gpuva']:
        text = item(s, 'fn ' + name)
        assert 'self.uat_inner.check_handoff()?;' in text
        assert text.index('check_handoff()?') < text.index('mapping_mutation()')
    g15 = MMU.parent / 'g15'
    uat = g15.joinpath('uat.rs').read_text()
    adopt = item(uat, 'pub(crate) fn adopt(')
    assert 'HandoffMemory::NoMapNormalNc' in adopt and 'new_adopted_bounded' in adopt
    assert 'HandoffMemory::Region => mmu::Uat::new_adopted(' in adopt
    for text in [g15.joinpath('hw.rs').read_text(), g15.joinpath('hw/t6031.rs').read_text()]:
        assert 'handoff_memory: super::HandoffMemory::Region' in text
        assert 'handoff_memory: super::HandoffMemory::NoMapNormalNc' not in text
    bootstrap = item(uat, 'pub(crate) fn bootstrap(')
    assert bootstrap.index('clean_lower(uat)?') < bootstrap.index('lock_adopted_handoff()?')
    sync = item(uat, 'fn sync_inner(')
    assert sync.index('Self::shadow_l2(uat)?') < sync.index('lock_adopted_handoff()?') < sync.index('shared.try_write64(')
    assert 'self.uat.check_handoff()' in item(g15.joinpath('boot.rs').read_text(), 'pub(crate) fn check(&self)')
    engine = g15.joinpath('engine.rs').read_text()
    assert 'self.check_uat(done)?' in item(engine, 'pub(crate) fn publish(')
    poll = g15.joinpath('poll.rs').read_text()
    assert 'self.check_uat(done)?' in item(poll, 'pub(crate) fn poll(')
    assert 'firmware.check_uat(&mut done).and_then(' in item(poll, 'pub(crate) fn submit(')
    lifetime = MMU.parent.joinpath('mmu/lifetime.rs').read_text()
    for marker in ['pub(super) fn mapping_commit(', 'pub(crate) fn retain_job(', 'pub(crate) fn retain_first_job(', 'pub(crate) fn wait_for_user_map_admission(']:
        assert 'self.uat_inner.check_handoff()?' in item(lifetime, marker)
    # Ownership boundary: callback errors precede all unlink/free operations.
    core = ROOT.joinpath('rust/kernel/drm/gpuvm/mod.rs').read_text()
    unmap = item(core, 'fn unmap_matching(')
    assert unmap.index('unmap(&mut *self.gpuvm.data.get(), typed_va)?') < unmap.index('bindings::drm_gpuva_unmap') < unmap.index('bindings::drm_gpuva_unlink_defer')
    c = ROOT.joinpath('drivers/gpu/drm/drm_gpuvm.c').read_text()
    assert 'drm_gpuvm_get(gpuvm)' in item(c, '\ndrm_gpuva_insert(struct drm_gpuvm *gpuvm,')
    create = item(c, 'drm_gpuvm_bo_create(struct drm_gpuvm *gpuvm,')
    assert 'vm_bo->vm = drm_gpuvm_get(gpuvm)' in create and 'drm_gem_object_get(obj)' in create
    m3 = MMU.parent.joinpath('mmu/m3.rs').read_text()
    assert 'if inner.uat_inner.bounded_handoff' in item(m3, 'fn map_io_m3_inner(')
    return 28


def run(rustc, mmu, ppl, label):
    with tempfile.TemporaryDirectory(prefix='asahi-bounded-ppl-') as tmp:
        path = Path(tmp)
        path.joinpath('fixture.rs').write_text(generate(mmu, ppl))
        build = subprocess.run([rustc, '--edition=2021', '--test', '-O', str(path/'fixture.rs'), '-o', str(path/'check')], capture_output=True, text=True)
        if build.returncode:
            raise RuntimeError(build.stderr)
        result = subprocess.run([str(path/'check'), '--test-threads=1'], capture_output=True, text=True, timeout=30)
        if label == 'production':
            print(result.stdout)
        return result.returncode, result.stdout + result.stderr


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rustc', default=os.environ.get('RUSTC', 'rustc'))
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    checks = audit()
    mmu, ppl = MMU.read_text(), PPL.read_text()
    code, output = run(args.rustc, mmu, ppl, 'production')
    assert code == 0, output
    mutations = [
        ('timeout leaves AP interested', mmu, ppl.replace('memory.ap_interest(false);\n            memory.barrier();\n            memory.fail();', 'memory.ap_interest(true);\n            memory.barrier();\n            memory.fail();', 1)),
        ('timeout skips failure latch', mmu, ppl.replace('memory.fail();', '', 1)),
        ('timeout skips completion barrier', mmu, ppl.replace('memory.ap_interest(false);\n            memory.barrier();\n            memory.fail();', 'memory.ap_interest(false);\n            memory.fail();', 1)),
        ('unlock clears AP before yielding turn', mmu, ppl.replace('self.memory.give_firmware_turn();\n        self.memory.barrier();\n        self.memory.ap_interest(false);', 'self.memory.ap_interest(false);\n        self.memory.barrier();\n        self.memory.give_firmware_turn();', 1)),
        ('failed lower mutation allowed', mmu.replace('''        self.check_handoff()?;
        if upper && self.bounded_handoff''', '''        if upper && self.bounded_handoff''', 1), ppl),
        ('failed GPUVA unmap allowed', mmu.replace('''        // Error leaves the GPUVA linked. Its GPUVM, VmBo, GEM and SG lease stay owned;
        // retaining page tables alone would not preserve the mapped backing.
        self.uat_inner.check_handoff()?;''', '', 1), ppl),
        ('cached mapping freed', mmu.replace('if self.0.uat_inner.bounded_handoff && self.0.prot.is_cached_noncoherent()', 'if false && self.0.prot.is_cached_noncoherent()', 1), ppl),
    ]
    for label, m, p in mutations:
        assert (m, p) != (mmu, ppl), label
        status, _ = run(args.rustc, m, p, label)
        assert status != 0, 'surviving mutant: ' + label
    print(f'{checks} source integration/ownership assertions; {len(mutations)} faulty variants rejected')
    if args.receipt:
        paths = [MMU, PPL, HERE/'check.py', HERE/'fixture.rs'] + [MMU.parent/'g15'/name for name in ['uat.rs','hw.rs','boot.rs','engine.rs','poll.rs']] + [MMU.parent/'mmu'/name for name in ['m3.rs','lifetime.rs']]
        receipt = {'scope': 'Actual production PPL core, transport, guard, failure checks, GPUVA unmap, rollback and KernelMapping destructor; synthetic host mutex/time/page tables/backing. No GPU/firmware execution or physical coherency claim.', 'rustc': subprocess.check_output([args.rustc, '--version'], text=True).strip(), 'source_assertions': checks, 'rejected_mutants': len(mutations), 'result': output, 'sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
        args.receipt.write_text(json.dumps(receipt, indent=2)+'\n')

if __name__ == '__main__':
    main()

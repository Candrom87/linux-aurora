# Bounded adopted handoff tests

Run from the kernel source tree with a host Rust compiler and linker available:

```sh
python3 -B tools/asahi/g15-bounded-handoff/check.py --receipt /tmp/ppl-result.json
```

The harness compiles the actual production PPL core, bounded transport, shared
lock/guard, failure checks, GPUVA unmap, rollback and KernelMapping destructor.
It also compiles the actual G15 backend failure bridge. Mutexes, the monotonic
clock, page tables, backing and backend completion/retention operations are
synthetic. Twenty-one tests cover both stuck firmware loops, yield/reassert,
unlock and timeout ordering, failure during a host-mutex wait, two host threads,
failed lower/upper admission, cached-map retention, GPUVA teardown and uncertain
rollback before logical-map insertion. Seven built faulty variants must fail.
Static source assertions check selection, root-before-mutex ordering, runtime
call sites and callback-before-unlink reference ownership. They are source
checks, not runtime tests of Linux GPUVM internals or firmware publication.

The new G15 policy is explicitly paired with `HandoffMemory::NoMapNormalNc`.
Both current board configurations remain `Region`; the experimental contract is
unselected. The bounded flag does not select M4 root layout/incarnation rules.
The legacy M3 DMA/MMIO helper rejects bounded adopted callers, which must use
the common mapping path with its failure ownership. Cached firmware mappings
have no qualified teardown and are retained until reboot on an unexpected drop.

After separately building the actual Asahi objects, inspect code generation:

```sh
python3 -B tools/asahi/g15-bounded-handoff/check_object.py \
  /path/to/build/drivers/gpu/drm/asahi/asahi_core.o --receipt /tmp/ppl-object.json
```

This inspects the emitted ARM64 acquisition and guard-drop bodies for full-system
barriers and ordinary shared-page accesses, including the unlock call boundary.
It does not inspect called mutex/time routines, prove every control-flow edge,
or prove hardware memory visibility. The 100 ms limit bounds firmware polling
after host-mutex acquisition; host scheduling, mutex waiting, barrier/fabric
completion and all GPU failure modes are outside that bound. No module, VM,
firmware, boot image, device or private machine data is used by these tests.

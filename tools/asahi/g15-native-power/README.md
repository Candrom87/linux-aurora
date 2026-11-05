# G15C kernel-native board arithmetic

This ports the explicit-input 23J220 numerical pipeline to an inactive Linux
API. `CONFIG_DRM_ASAHI_G15_NATIVE_POWER` defaults off and requires ARM64 kernel
FPU support plus the existing G15 backend. No platform caller, T6034 hardware
match, source acquisition, register access, power operation or firmware start
is added. Existing board profiles are unchanged.

The kernel entry points in `g15/power/native.h` accept ordinary immutable input
buffers with an integer ABI. `asahi_g15c_source_packet()` imports main/SRAM and
CS/AFR properties, checks the shared rail and explicit identity geometry,
decodes supplied coefficient words, derives activity popcounts, applies both
native selector streams, computes the selected power rows and encodes the
existing big-endian board property. This does not qualify the provenance,
same-boot identity, firmware or power state of any supplied input. The current
record accepts at most 14 raw rows and 16 selected rows, including repeats.

`asahi_g15c_decode()` and `asahi_g15c_board_packet()` are separate checked entry
points for already acquired inputs. All public outputs stay unchanged on
failure. Model refusals use the existing `G15C_*` status; context/allocation
refusals use Linux errno. The allocating API requires sleepable task context.
Caller-owned spans must be readable, immutable and disjoint from the output.

## CPU ownership and arithmetic

Parsing, selection, allocation and packet encoding stay outside FP sections.
The integer wrapper rejects interrupt/atomic/nested use and unavailable or
unfinalized CPU support, pins execution, rechecks SIMD availability and obtains
the generic kernel FPU context. It preserves FPCR/FPSR, sets the canonical IEEE
environment, runs the original environment probes and calculation, restores
both registers and releases ownership on every returned status. User vector
state protection is delegated to the actual arm64 kernel FPU API. No Apple
vendor FP control is accessed. The fixtures below model that kernel API; they
do not establish runtime scheduling, interrupt nesting or userspace vector
restoration in a booted Linux kernel.

The original arithmetic, probes, selector, property importer and source bridge
are copied exactly from the reviewed m1n1 source. `power/provenance.json` binds
them and the licenses by source hash. Arm double `pow` is the pinned v26.07 MIT
source; the existing m1n1 Arm `powf` and data are also unchanged. Private header
adaptations supply only the freestanding helpers they consume, separate the
single/double helper namespaces and avoid a Linux attribute-macro collision.
The source adapter's decode symbol is rebound to the owned kernel entry.

FP translation units use `CC_FLAGS_FPU`; all ownership/import/selection units
retain `-mgeneral-regs-only`. LTO is removed across the boundary. The original
source-order binary32/binary64 rounding is retained, without fast math or
implicit contraction. Explicit FMA operations inside Arm's double `pow` remain
intentional. The driver's existing approximate software F32 is not substituted.
No claim of all-input equivalence to Darwin's math library is made.

## Reproduction

On AArch64 Linux, from this kernel checkout, with a working C/Rust toolchain:

```sh
python3 tools/asahi/g15-native-power/check.py --cc gcc --mutants --receipt /tmp/native-gcc.json
python3 tools/asahi/g15-native-power/check.py --cc clang --mutants --receipt /tmp/native-clang.json
python3 tools/asahi/g15-native-power/check.py --cc gcc --ubsan --receipt /tmp/native-ubsan.json
```

These execute actual source in ordinary userspace with invented data. Kernel
allocation, preemption, context availability and ownership services are modeled;
FPCR/FPSR control instructions and IEEE probes execute on the CPU in this
process. Each call restores the process environment before returning to Python.
No module is loaded and no boot image, device, saved ADT or private input is read.

Per profile, the final receipt records 1,456 imported-row packet comparisons,
728 raw-source packet comparisons, 896 coefficient decodes and 343 atomic
refusals. All 2,184 packets also pass the actual core-only Rust board reader,
including both grouping streams, relative-power words and all independent
128-bit masks. The hosted-libm reference uses the same original source-order
formula with an independent math implementation; this is a sampled integer
output comparison, not exhaustive or physical calibration validation.

The refusal controls cover invalid context, nested ownership, missing CPU
support, allocation failures before and after coefficient decoding, malformed
properties/identity, IEEE probe failure, nonfinite coefficients, unsafe selected
indices and late numerical failures. Seven initial FP control combinations
include nondefault rounding/flush/default-NaN controls, with nonzero FPSR. Five
built faulty variants must fail for their specific expected reason: omitted
FPCR restoration, omitted FPSR restoration, omitted canonical environment,
omitted nesting refusal and incorrect raw-prefix row selection.

For actual Kbuild validation, start with the reviewed G15 development config,
enable `DRM_ASAHI_G15_NATIVE_POWER`, then build `drivers/gpu/drm/asahi/` in a
separate output directory using the checkout's supported compiler. Inspect it:

```sh
python3 tools/asahi/g15-native-power/check_objects.py --output "$out" --receipt /tmp/native-objects.json
```

The actual 15 objects and partial `asahi.o` linkage are checked for correct
general-register/FP compilation, lack of LTO, no FP/SIMD operations in the six
integer units, no implicit FMA in the model and no unresolved hosted math.
Observed SP subtractions are recorded; compiler frame-size diagnostics remain
enabled. A separate Kbuild invocation overriding the feature to `n` must produce
an `asahi.o` without any of these API/core/import symbols:

```sh
make O="$out" ARCH=arm64 LLVM=1 CONFIG_DRM_ASAHI_G15_NATIVE_POWER=n drivers/gpu/drm/asahi/
python3 tools/asahi/g15-native-power/check_objects.py --output "$out" --disabled
```

The receipt covers the full Image/modules/DTBs build of
`7.2.11-m3max-native1`, including successful modpost and the new APIs in the
final Asahi module. There are nine pre-existing Rust atomic deprecation
warnings and no other warnings or errors. The reviewed config uses LTO_NONE;
other compiler/configuration matrices are not established.
No installation, physical boot, GPU firmware READY, graphics/compute workload,
VRR or Neural Engine operation is established. Same-boot input acquisition,
power/firmware qualification and a physical runtime test remain necessary.

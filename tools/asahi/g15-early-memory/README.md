# J514M/T6034 kernel-contained early memory preparation

This is a source implementation for a separately selected experimental kernel.
It addresses the ordering problem that a Limine EFI kernel one-shot cannot
select experimental code in an earlier installed m1n1 stage. All new runtime
work here is inside the selected kernel. No bootloader replacement is needed
to execute this candidate path. This does not establish that it will accept
the real same-boot regions, or that they survived the whole boot history.

`CONFIG_APPLE_T6034_GPU_EARLY_MEMORY` defaults to **n**. A kernel built with it
also needs exactly one `asahi_t6034_gpu_memory=1` early parameter. Opt-out
returns without metadata access. This is built-in early code, not a module to
load into an active desktop. No GPU-compatible match, active G15 profile, GPU
power operation, firmware admission/start, READY or accelerated workload is
added. The GPU node stays disabled even on successful preparation.

The reservation hook executes at the end of `arm64_memblock_init`, after ordinary reserved
memory and initrd/kernel reservation, before `paging_init`. This is after EFI
has rebuilt memblock's memory list from its map. Both source order and the
actual enabled/disabled `init.o` references are checked. A `mem=` restriction
refuses preparation instead of adding memory back around it.

## Source and ownership conditions

The inherited FDT must be bounded and fully valid, name J514M/T6034, identify a
unique disabled T6034 GPU via a canonical alias, and contain exactly the public
disabled zero-size sentinel owners referenced by its four UAT names. The
retained ADT must have one enabled normal-memory phram owner, with an exact
bounded tuple. Before copying its payload, the runtime verifies full normal-RAM
coverage and full reservation coverage, not merely one overlapping reserved
address. The copy uses the existing chunked early metadata read API.

The bounded ADT parser checks the complete tree before exact unique-child and
unique-property lookups. It requires J514m, gpu,t6034, nonoverflowing physical
DRAM bounds and four aligned, disjoint fixed-ABI UAT regions inside that DRAM.
It does not authenticate firmware or bless unrelated private/native inputs.

Every UAT extent must be disjoint from **every** EFI descriptor, all inherited
FDT memory tuples, conflicting active reservations and the current memblock
memory/reserved lists. Invalid descriptor geometry refuses. Nothing previously
described as EFI reserved, conventional, boot services, runtime or MMIO memory
is reinterpreted by this route. Table extents must fit the actual selected
linear mapping. Only native 4-KiB or 16-KiB granules are accepted. Active
dynamic size/alignment reservations refuse, so the later rescan cannot allocate
or initialize them twice.

These checks describe the inherited allocation domain and current kernel
state. They do not enumerate all earlier writes, attest the bootloaders, prove
firmware inactivity, or eliminate arbitrary nonstandard aliases. The exact
installed boot chain and physical preservation still need qualification.
Do not relax a refusal into a late reservation of potentially reused RAM.

## Commit and lifetime

All planning operates on a private FDT copy. A successful plan populates the
four existing owners, adds the handoff no-map policy, and appends the three
table extents to the FDT memory description. It never enables the GPU. A new
tree is allocated in already usable/reserved normal RAM and filled through
bounded temporary mappings. No pointer to init-only staging arrays survives:
the published tree has an independent memblock reservation.

Only after preflight/copy succeeds does the runtime reserve all four UAT
regions, then add the three table regions to memblock. Reserving first also
protects against allocation during memblock array resizing. The handoff remains
outside memblock memory, excluding its standard linear alias; tables retain
the intended reserved normal-RAM/WB policy. No GPU table/handoff contents are
read or written. No GPU registers are accessed.

Publication is a separate phase immediately after `paging_init()` and before
device-tree unflattening. It uses the reserved copy's normal linear mapping;
the original FDT fixmap remains intact for any earlier pointers. The copy's
reservation, normal-memory membership and mapping class are checked first.
After tree publication, actual early verification refreshes the flat-tree
pointer, physical pointer, root cells and CRC. The reservation scan refreshes
static inventory counts for the later unflatten/owner scan. Every region's
reservation and mapping class is rechecked. Commit failures panic rather than
publishing partially prepared ownership. Earlier refusals preserve the original
tree and do not reserve/add GPU memory. Failed ordinary temporary allocation
cleanup may conservatively retain normal memory; it cannot make GPU memory
available.

Kernel E102's independent retained-ADT provenance, owner and actual PFN/classifier
admission remain required before any eventual GPU mapping. The marker is not
authentication. This patch does not select that G15 configuration or bypass
its gates.

## Source verification

Run in the source checkout, with a fresh receipt path:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B tools/asahi/g15-early-memory/check.py \
  --kernel . --cc gcc --page-shift 14 --receipt /tmp/early-gcc14.json
```

Repeat with `--page-shift 12`, a working Clang compiler, or GCC `--ubsan`.
The canonical receipt binds every production/test input and extracted OF body.
Each GCC/Clang granule run and the GCC UBSan run passes 836 checks: 85 runtime/
parser scenarios, 740 incomplete ADT prefixes ending exactly at an inaccessible
anonymous CPU page, five malformed FDT controls and six mapping/phase checks.
Four compiled mutants
fail their intended assertions. Precommit refusals, reserve-before-add order,
copied-tree lifetime, GPU-disabled state and post-mutation panic paths are covered.

Actual static OF reservation scan bodies run, including their owner counts.
The actual FDT verification and root-cell scan bodies also run, checking the
published virtual/physical pointers, root cells and CRC update. CRC uses a
hosted scalar implementation; no kernel page table is accessed.
Getters, node callbacks, allocators, EFI map, early mappings and panic are
modeled. The unchanged ARM64 `pgattr_change_is_safe()` body also runs against
synthetic 48-bit physical-address PTE helpers. A live valid-to-valid PFN change
is rejected; creating a mapping and changing only permissions are controls.
The fourth mutant reinstates the old FDT fixmap replacement and must fail at
that exact guard. Separate checks forbid publication before modeled paging,
verify original-tree lifetime and make repeated publication a no-op.
No real memblock allocator, OF driver callback, page table, early boot or hardware
is executed by the host fixture. Source order checks cover the real placement
of reservations before paging and publication after paging/before unflattening.
All children have 60-second execution limits.

The target check now builds a complete ARM64 Image, modules and DTBs with the
option enabled at 16-KiB granularity. Separately built disabled-config setup/init
objects verify that neither phase is referenced when disabled. Enabled objects
reference their respective hooks. Runtime/parser emitted instructions contain
no FP/SIMD registers. The full libfdt checker was declared but absent from the
kernel's built subset; its unchanged dual-licensed implementation is built only
for the new option. These are build checks, not physical hardware qualification.

A separate unprivileged live-FDT shape check used a private CPU copy and an
invented layout. It accepted the metadata template, not actual GPU extents.
No private artifact/digest was retained or published, and no ADT payload was
read by that check.

## Failed mem1 boot and correction

The original a3369351c path called `fixmap_remap_fdt()` with a different
physical copy while the original mapping was still valid. That reaches
`init_pte()` through `create_mapping_noalloc()`; the actual ARM64 guard forbids
changing a live mapping's PFN. The old host mapping stub missed this rule.
The two-phase publication above avoids both that violation and invalidating
earlier FDT pointers. It does not relax the guard or replace live PTEs.

The separately authorized mem1 hardware attempt produced a user-reported black
screen and restart, with subsequent normal stock return and cleared live/saved
one-shot verified. No crash log identified the failing stage, so reaching this
source defect during that attempt remains unproven. A CPU-only kernel boot
without the Mac EFI/metadata path does not establish the activated path works.
The corrected source has not been installed or armed. Before any hardware use, prepare the complete
reviewed one-shot image/procedure and verify normal Linux fallback. The new
metadata/early-mapping experiment requires its own authorization; fulfilled
collector approvals cannot be reused. Firmware identity/ABI, native same-boot
board inputs, power/suppliers, complete startup and real GPU work remain open.

## Full-kernel CPU regression

`cpu_boot.py` runs the actual built kernel under QEMU TCG with Cortex-A76 and
entirely invented EFI handoff tables, retained ADT and FDT metadata. It does not
execute EFI firmware or emulate/pass through a GPU or any host device. The
ordinary emulated RAM backs the invented excluded extents. EFI runtime services
are disabled; no root disk is attached. No kernel test-only admission bypass is
added. The original metadata parser/planner validates the invented inputs first.

QEMU normally rewrites a supplied FDT's RAM range, losing the intended
exclusions. An owned debugger connection restores and reads back the validated
invented tree in guest RAM before any kernel instruction executes. The fixture
asserts the QEMU direct-boot trampoline format and the guest RAM bounds. It
uses a local Unix socket and runs without OS administrator privileges; an
execution sandbox may require permission for that socket. All guest processes
are bounded and terminated when done. Guest entropy is removed for deterministic
old/new input comparison; no host or private metadata is used.

```sh
python3 -B tools/asahi/g15-early-memory/cpu_boot.py --kernel . \
  --image /path/to/new/Image --qemu /path/to/qemu-system-aarch64 \
  --out /tmp/fdt-cpu-new
```

The old exact mem1 Image is tested with the same script and
`--expect-old-pfn-bug`. It must reach the `init_pte()` mapping-safety BUG source
line through `fixmap_remap_fdt()` and is stopped there. The corrected Image must
report successful preparation and continue through paging/OF setup to the
expected no-root-disk panic. The canonical receipt records both outcomes.
This validates this kernel path with real emulated page tables; it neither
explains the unlogged Mac failure conclusively nor qualifies firmware history,
Apple cache behavior, driver startup, or a single GPU workload.

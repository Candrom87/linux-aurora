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

The hook executes at the end of `arm64_memblock_init`, after ordinary reserved
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
Each GCC/Clang granule run and the GCC UBSan run passes 830 checks: 85 runtime/
parser scenarios, 740 incomplete ADT prefixes ending exactly at an inaccessible
anonymous CPU page, and five malformed FDT controls. Three compiled mutants
fail their intended assertions. Precommit refusals, reserve-before-add order,
copied-tree lifetime, GPU-disabled state and post-mutation panic paths are covered.

Actual static OF reservation scan bodies run, including their owner counts.
Getters, node callbacks, allocators, EFI map, early mappings and panic are
modeled. No real memblock allocator, OF driver callback, early boot or hardware
is executed by the host fixture. Source order checks cover the real placement
of the hook before paging. All children have 60-second execution limits.

The target check builds twelve real ARM64 kernel/libfdt objects with the option
enabled at 16-KiB granularity. A relocatable partial link resolves all fdt_*
references. The enabled init object references the hook; a separately rebuilt
disabled-config init object does not. Runtime/parser emitted instructions contain
no FP/SIMD registers. This is not a full vmlinux/Image link. The libfdt full
checker was declared but absent from the kernel's built subset; its unchanged
dual-licensed implementation is now built only for the new option.

A separate unprivileged live-FDT shape check used a private CPU copy and an
invented layout. It accepted the metadata template, not actual GPU extents.
No private artifact/digest was retained or published, and no ADT payload was
read by that check.

No image was installed or armed. Before any hardware use, prepare the complete
reviewed one-shot image/procedure and verify normal Linux fallback. The new
metadata/early-mapping experiment requires its own authorization; fulfilled
collector approvals cannot be reused. Firmware identity/ABI, native same-boot
board inputs, power/suppliers, complete startup and real GPU work remain open.

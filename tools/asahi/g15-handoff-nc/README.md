# Inactive G15 handoff Normal-NC admission

`HandoffMemory::NoMapNormalNc` is a separate, unselected J514M/T6034 contract
for the PPL handoff page. Existing T6030/T6031 configurations select `Region`;
the T8122 adoption path and shared mapping classifier are unchanged. There is
no new GPU match, enabled node or working T6034 profile.

The retained native software's real-time policy resolves to Normal inner/outer
noncacheable. Reserved System RAM currently uses a WB linear mapping. The new
gate requires the boot loader to exclude the handoff page from that mapping,
rather than creating a conflicting NC alias of ordinary RAM.

For an explicitly selected configuration, admission requires:

- Exact J514M/T6034 machine and GPU identity, plus
  `asahi,g15c-handoff-memory = "no-map-normal-nc-v1"` on the GPU node.
- This boot's retained ADT: structurally valid tree, unique exact NUL-terminated
  `chosen`, `arm-io` and `sgx` node names, T6034 GPU compatibility, nonzero
  nonoverflowing physical DRAM bounds, and exact handoff-base/size agreement.
- A unique named `handoff` phandle referencing the enabled static
  `/reserved-memory/uat-handoff` owner: exactly one 16-KiB range, empty
  `no-map`, no `reusable`, two address/size cells and empty parent `ranges`.
- No other enabled same-name owner or overlapping static reservation,
  including later tuples. A malformed competing `reg` refuses admission.
- The common mapping classifier returns only `MEMREMAP_WC`, and every PFN
  reports that it is outside the standard Linux linear mapping. On arm64 this
  memory mode creates Normal-NC. Both `Mapping::region` and the shared adopted
  UAT retain that same classifier; no separate WB handoff path is introduced.

These are snapshot admission checks for the standard Linux linear alias.
Neither a live OF node nor a `no-map` property alone proves early reservation
success. The gate does not reserve the resource against arbitrary kernel
mappers, inventory every possible alias or prove AP/firmware coherency. The
normal-NC class is not a claim of matching every native PTE/shareability bit.
The copied ADT remains trusted boot metadata, not cryptographic attestation.

Table regions retain their existing policies. The existing pre-open check
requires all four regions to be reserved System RAM, so a mixed no-map handoff
uses the existing post-open order. The gate does not silently relax that check.
Supplier admission still precedes this part of the boot path; these checks do
not claim that every platform PM operation is absent.

The boot-loader producer/caller and actual memory exclusion still need to be
implemented and qualified. A marker alone does not change cache policy or
enable hardware. An independent remaining issue is the unbounded legacy shared
handoff lock; its outer wait deadline does not bound lock acquisition. That and
barrier-domain/lifetime qualification must be resolved before proposing a
bounded physical firmware-start experiment. No cache-policy test on hardware
or accelerated workload is established here.

From the kernel source root, with the compiler/toolchain environment available:

```sh
python -B tools/asahi/g15-handoff-nc/check_c.py --kernel .
python -B tools/asahi/g15-handoff-nc/check_rust.py --kernel . --rustc /path/to/rustc
```

The C runner compiles the actual helper body with simulated OF/resource/PFN
APIs and checks 62 cases at each of 4-KiB and 16-KiB host page sizes, including
all reference releases. It does not run real OF or mappings. The Rust runner
compiles the actual ADT parser and provenance gate against 39 synthetic normal
buffers. A mutant using the legacy prefix-name lookup is rejected. C execution
has a 30-second deadline and Rust fixtures a 10-second deadline. No VM, module,
device, privileged read, boot operation or private captured input is used.

# Native DVFS/SRAM input through to the G15C hardware record

The E112 source-to-packet API calculated model/reference words but did not carry
the separate imported main/SRAM/CS/AFR tables into the firmware record. The
existing OPP builder derives SRAM floors and voltage-sorted state groups. Those
are separate input semantics and cannot substitute for native imported data.

`asahi_g15c_source_bundle()` now returns one integer-ABI result containing the
packet, full native imported tables and three independent clock-domain caps.
All parsing and bounded reimport of the caller's immutable spans occur outside
FPU sections. This is one supplied source snapshot, not a second acquisition.
The result lives in allocated scratch and the public output is unchanged on
every refusal, including a cap-context failure after numerical calculation.
There is no device access, new hardware match, acquisition or platform caller.

The caller supplies an explicitly qualified old-host CoreConfig selector and
revision. These are not assumed to be an arbitrary GPU identity-register word.
The pinned 23J220 G15X getter uses count-minus-one normally, but selector0x18 /
CoreConfig revision1 uses static main/CS/AFR caps7/5/5. The target-scoped helper
refuses other selectors and caps outside the supplied tables. The main cap is
indexed in the selected primary table. Establishing this context from the
actual same-boot inputs remains work for the future acquisition path.

Core-only `record::build_native()` accepts the explicit imported representation
matching C (`NativeMain1096`, `NativeAux328`, `NativeTables1764` bytes). It obtains
all performance rows from it, retains both native maps including repetitions,
and checks raw/selected bounds, cap bounds and the shared auxiliary rail. It
does not derive grouping from voltages. Imported u32 Hz is divided by1,000,000
only at record serialization. Original per-lane imported core and SRAM values
are copied without the OPP path's floor substitution.

The primary record has eight core/SRAM lanes for each selected row; the
secondary record copies the active four imported lanes and leaves its unused
lanes zero. CS and AFR retain independent counts/frequencies and their imported
per-channel SRAM. Each auxiliary header's first word is count-minus-one; its
second word is the independently produced cap. These rules were checked against
the pinned retained source, not inferred from the public DT schema. The main
cap also has a separate runtime consumer; this patch retains it in the bundle
and does not claim that the entire firmware graph consumes it correctly yet.

Other record metadata and layout are still explicit caller inputs. The record
builder is a scratch writer: publish only after complete success. It does not
attest source identity, firmware/build compatibility or electrical safety.
The production board adapter requires imported tables for a native profile and
refuses that profile when only the ordinary OPP adapter is supplied. No current
qualified acquisition caller supplies them, and no T6034 match is enabled.
Existing board profiles retain the prior path and synthetic record bytes.

## CPU/source reproduction

On AArch64 Linux with the supported C/Rust toolchain, from this checkout:

```sh
python3 -B tools/asahi/g15-native-dvfs/check.py --cc gcc --mutants --receipt /tmp/dvfs-gcc.json
python3 -B tools/asahi/g15-native-dvfs/check.py --cc clang --mutants --receipt /tmp/dvfs-clang.json
python3 -B tools/asahi/g15-native-dvfs/check.py --cc gcc --ubsan --receipt /tmp/dvfs-ubsan.json
```

The actual C source/import/model/bundle path feeds actual Rust complete-record
construction. Independent Python import/serialization checks cover each native
performance word, including unused zero tails, conflicting legacy SRAM floors,
last-table frequency overwrite, missing-SRAM defaults, fractional-uV truncation,
wide auxiliary-Hz narrowing, independent/repeated maps and non-top caps. All
inputs are invented; no device or private saved ADT is read. FPCR/FPSR execute
in userspace under E112's modeled kernel services. No module or boot image is
loaded. Full kernel FPSIMD scheduling and hardware operation remain untested.

The cap helper additionally has a bounded selector/revision/count grid with
unchanged-output checks on refusals. Malformed raw shape, shared rail, cap and
selected indices are refused in the Rust path. Five built faulty variants must
fail: primary, secondary or auxiliary SRAM floor substitution; forced top-state
auxiliary caps; and voltage-sorting the native maps. Serialization mutations
are checked to change exactly the intended independent-oracle byte ranges.

The existing `g15-relative-powers-tests.py` can run against the pre-change
`record.rs` to prove complete J516C synthetic record identity and retain its
five relative-power fault controls. The off-tree physical fixture tests stay
ignored. Target Kbuild checking uses `g15-native-power/check_objects.py`, now
covering16 C objects and seven general-register-only units. No inference from
these checks establishes physical acceleration, native display VRR or ANE.

The final three profiles each cover 139 bundles, 95,354 independently checked
performance words, 748 atomic C refusals, 4,116 cap cases and 1,112 Rust geometry
refusals. Both ordinary compiler profiles reject all five record mutations.
The complete `7.2.11-m3max-native2` Image/modules/DTBs build and modpost pass;
the final module contains the bundle API. The source compilation reports the
nine existing Rust atomic deprecation warnings and no new warning. A sequential
feature-disabled relink omits the native symbols, then the enabled full build
is restored and audited. Nothing is installed or armed.

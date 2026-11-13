# Experimental G15C native board construction

`Board::DeviceTreeNative` requires one immutable original source, its matching
native firmware/device context and explicit register words. `prepare_board`
computes, binds and validates the complete bundle (packet, imported raw
main/SRAM/CS/AFR tables and separate caps), then constructs HardwareData, Globals
and Power. A short `asahi,g15c-native-board-data` property or raw bundle cannot
replace that production input context. Normal T6034 startup still refuses
before its Regs::new/hold_power until firmware and fresh inputs are qualified. This does not describe all
earlier platform/supplier or firmware-identity operations.

The checked integer C producer API is in `g15/power/native.h`. The source scope
is 23J220; neither this name nor its transport tag is a firmware ABI admission,
source authentication, same-boot attestation or physical power-unit/limit proof.
Machine/loaded-firmware identities and other startup checks remain unchanged.
There is no qualified acquisition/start caller yet and no physical acceleration.

The owned packet uses exactly `16+3*N` big-endian u32 cells, `N=2..16`:

| Cells | Value |
|---|---|
| 0 | Original transport tag `0x23c22001` |
| 1 | Expected chip ID |
| 2 | Opaque nonzero native model word; units are not inferred |
| 3 | Selected count N |
| 4..7, 8..11, 12..15 | Independent fast-die, SoCHot, enabled/alarm masks; MS cell first |
| 16..16+N-1 | Primary indices |
| 16+N..16+2*N-1 | Secondary indices |
| 16+2*N..16+3*N-1 | Complete relative-power words, including zero and explicit top |

Both streams begin with zero and contain bounded nonzero active indices. The
bundle checks every index against its imported raw count, all geometry/caps and
the shared auxiliary rail. It preserves repeated/nonmonotonic native selections;
no voltage grouping is substituted. The existing base-state invariant remains
required. The raw capacity is fourteen and the selected capacity sixteen.

Native main/secondary core and SRAM values come exclusively from imported data.
Legacy SRAM floors and cached OPP rows cannot substitute. CS/AFR retain separate
counts/frequencies and imported SRAM; their two header words carry count-minus-one
and independent cap. Globals initial targets use source full-scale interpolation
and minimum duty; its main cap is separate. Maximum selected frequency is scanned
without reordering. Pstate reporting retains its owned selected-table snapshot
before the record becomes firmware-visible. Legacy bytes/status remain unchanged.

`DRM_ASAHI_G15_BOARD_KUNIT_TEST` is default-off, built-in-only and requires the
native arithmetic option. On the marked QEMU fixture, real OF parser tests retain
the 88..256-byte bounded short-packet checks. Packet-only startup is refused.
A real C producer bundle then crosses the checked Rust ABI and configured
preflight into all three images. That test uses private disabled OF nodes and an
unregistered platform device with conflicting legacy caches/floors and no OPP
phandles. It checks selected maps, SRAM, caps, masks, relative words, status,
maximum frequency and six-channel mapping. Changesets/references are released.
Other machines skip; no GPU registers, power helper or firmware startup runs.

These tests establish software construction, not this Mac's provenance or safe
power limits. Exact immutable input ownership, firmware/source qualification,
physical provider/power/cache/memory/startup and independently checked rendering
remain required. No fixture Image or child is suitable for Mac installation.


The native entry now requires an immutable `NativeAdt`, an exact
`NativeFirmware` source/device context and explicit register-only `Words`.
Performance properties, sensor rows and enabled MTR membership come from that
one private copy. Equal-content copies have distinct identities, stable across
Rust object moves. Only the internal factory creates its integer C input; masks
are selected from membership and the scoped contract rather than supplied in
`Words`. The C output is heap allocated and checked/bound before image writing.
The model's -1/-2/-3 statuses map to EINVAL while real Linux errno is preserved.

Native export copying first requires one enabled, non-reusable, non-no-map,
16-KiB-aligned reserved-RAM owner, at most 2 MiB. The source's firmware segment
metadata must agree before native TEXT copy, and a board/model-tagged descriptor
must be eligible before measurement. All seven current real descriptors remain
untagged; no physical native firmware pairing is established. Legacy prefix
policy and complete image tuples stay unchanged. Ordinary probe supplies no
fresh register words and cannot start the GPU.

The KUnit owner path reads the real reserved-RAM fixture export, checks actual
same-copy/device association and runs the typed producer into all three images.
Its positive firmware context is explicitly synthetic and test-only, not loaded
firmware measurement; its descriptor appears in no production profile. This is
not proof of fresh power-owned register acquisition or physical compatibility.
Native controller properties now come from that same original source too.
The typed source owns its borrowed settings alongside the bound bundle; record
construction rejects settings borrowed from another equal-content copy. All
record/Globals/Power controller inputs bypass OF for native profiles. Legacy
profiles retain their OF path. The reader preserves optional reset/delta
overrides, decodes little-endian scalar bits without float conversion, and
requires complete contiguous power-zone triples, at most five. Partial/holey
zones and a sixth row refuse rather than silently dropping configured inputs.
The two legacy SRAM-floor slots stay unused/zero; the native bundle supplies
every actual SRAM voltage. This is not a fallback power or thermal policy.

The Linux fixture poisons every independent OF scalar and supplies malformed
OF zones, then checks original controller words in all three startup images,
including optional overrides, two power zones, a sample-period gain product and
the standby timer. A separate wrong-owner case refuses before record writing.
The pure CPU controller suite checks scalar/order/endian/missing/duplicate
rules, zones, original-copy identity and borrow escapes on invented data only.
Qualified provider/power/cache/memory/startup remains required.

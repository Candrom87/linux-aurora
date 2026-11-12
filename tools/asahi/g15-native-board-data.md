# Experimental G15C native board construction

`Board::DeviceTreeNative` now requires one complete immutable computed bundle:
the packet, imported raw main/SRAM/CS/AFR tables and three independent caps.
`prepare_board` binds and validates them together, then constructs HardwareData,
Globals and Power. A short `asahi,g15c-native-board-data` OF property alone is
insufficient; normal T6034 startup refuses before its Regs::new/hold_power until
a qualified input/provider path supplies the bundle. This does not describe all
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

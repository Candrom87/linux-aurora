# Experimental G15C native board-data transport

`Board::DeviceTreeNative` consumes `asahi,g15c-native-board-data` from the GPU
node. T6034/J514M now requires this source in board preflight and never falls
back to J516C reference words. Supplying this property alone does not register,
enable or admit another GPU. Board/firmware identities remain unchanged.

This is an original boot-loader/kernel transport, not an Apple firmware ABI or
an authentication mechanism. The producer must establish the appropriate
version, branch, same-boot inputs and independent mask provenance. Machine,
firmware identity, reserved-memory, supplier and startup checks remain separate.

The property is an array of big-endian u32 DT cells. The OF decoder produces
host-endian values; the record serializer writes bounded little-endian bytes.
For selected state count `N`, it contains exactly `16 + 3*N` cells:

| Cell(s) | Meaning |
|---|---|
| 0 | Original contract tag `0x23c22001` |
| 1 | Chip ID, required to equal the selected kernel configuration |
| 2 | Native model reference word, unchanged; physical mW units are not inferred |
| 3 | Selected state count `N`, 2 through 16 |
| 4–7 | Independent fast-die mask, most significant cell first |
| 8–11 | Independent SoCHot mask, most significant cell first |
| 12–15 | Independent alarm/enabled mask, most significant cell first |
| 16 through `16+N-1` | Ordered primary indices into the raw main OPP table |
| `16+N` through `16+2*N-1` | Ordered secondary indices |
| `16+2*N` through `16+3*N-1` | Complete native relative-power words |

The raw property must be 88–256 bytes, divisible by four, before allocation.
No omitted cell or independent mask is supplied from another value. Both maps
must start with zero, contain nonzero in-range later indices and agree exactly
with the actual primary and secondary tables constructed from the device's
operating points. Repeated indices are preserved by transport; a conflicting
record construction is refused. The existing raw-table capacity remains 14;
the selected-array representation has the firmware's 16-slot capacity.

Every relative-power word is transported, including state zero and the native
top word. State zero must be zero; the top is not forced to 100. The producer
must compute its model using the matching selected rows and CS-state semantics,
rather than copying a raw-row prefix or mixing an unrelated selection. The
packet consistency checks do not prove that producer obligation by themselves.

The parsed reference owns its arrays, survives through synchronous image
construction, and leaves only owned images afterward. Validation occurs before
this admission path reaches `Regs::new` and its `hold_power` request and before
firmware startup. This does not describe all earlier platform/supplier PM.
Native-source logs omit actual model values, masks and board-data hashes.

The legacy J516C reference is chip-scoped and retains its established record
bytes. Relabeling that reference as another chip is refused. A new profile
still needs its complete reviewed configuration and explicitly authorized
physical testing; packet parsing or a passing synthetic test is not support.

`DRM_ASAHI_G15_BOARD_KUNIT_TEST` adds an optional built-in test bridge, restricted
to the marked QEMU metadata fixture. It uses real OF changesets/properties and
an unregistered platform device, then calls the production Rust property getter
and configured T6034 preflight. Its performance rows, regions, sensor rows,
controller words, masks and powers are invented. It never calls `Regs::new`,
power helpers or firmware startup. Changesets and device/node references are
released. Other machines skip the suite. This verifies metadata delivery and
board-image construction, not this Mac's source provenance or safe power limits.

The admitted record's selected count now supplies all three Globals performance
caps. Native pstate reporting retains an owned copy of its complete selected
primary frequency table before the record becomes firmware-visible; it does
not use the fixed legacy nine-entry map or reread firmware-mutated tables.
The legacy reference's bytes/status text stay unchanged. The kernel preflight
test also checks every transported mask/ratio, the model word in all images,
selected caps, selected top frequency and the six-channel T6034 mapping size.

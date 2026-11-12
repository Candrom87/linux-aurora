# Current native bundle, record and Globals source tests

These helpers transfer the independent CPU oracles from reviewed source
3052d18a522c0b680a001a61556e622301695886 and
ce7978ff4f7394cb2b3e65f798ef7046eecf53a4. The fixture also supplies the newer
explicit record chip ID; it does not relabel a legacy reference. All properties,
identity words, masks and coefficients are invented, and no device/boot operation
runs. Kernel ownership services are modeled here; the actual-kernel suites add
that execution scope separately. No historical receipt is copied as current proof.

With AArch64 C/Rust tools and the process-local no-core guard, run from this
checkout, selecting a new private receipt path:

```sh
python3 -B tools/asahi/g15-native-dvfs/check.py --cc gcc --mutants --receipt /tmp/native-dvfs-gcc.json
python3 -B tools/asahi/g15-native-dvfs/check.py --cc clang --mutants --receipt /tmp/native-dvfs-clang.json
python3 -B tools/asahi/g15-native-dvfs/check.py --cc gcc --ubsan --receipt /tmp/native-dvfs-ubsan.json
```

The actual C producer feeds complete Rust records and Globals patches. Separate
Python oracles check every imported/native performance word, zero tails,
conflicting legacy SRAM floors, both ordered/repeated selections, independent
caps and initial-target interpolation. Eight compiled controls must be detected:
three SRAM-floor substitutions, cap=count-1 substitution, native-map voltage
sorting, two initial-target substitutions and main-cap substitution. CPU source
snapshots and complete-output comparisons do not qualify native input provenance,
firmware, physical limits, GPU startup or acceleration.

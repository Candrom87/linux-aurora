# Current exact native firmware/source policy checks

Transfers the reviewed firmware policy, same-copy/device association and native
segment metadata tests from d684b99d675e43e3b459afd871c8ac4b3b70828c,
55e2cb640bd18d5eff8a43b2c4cc03e69b1a7980 and
95c0ac822da91b4df596a292dfab93071d99ddb7. The current legacy caller keeps its
explicit allow-prefix policy, including measurement-only empty profiles.
All seven real image descriptors remain untagged for native model production.
No historical receipt is copied as current evidence.

```sh
python3 -B tools/asahi/g15-native-firmware/check.py --mutants --receipt /tmp/native-firmware.json
```

Actual Rust policy/lookup/source/segment bodies run against modeled RAM,
allocation, crypto, machine, Arc/ARef and device services. Synthetic data only;
no module, physical mapping or device operation. The negative compiler control
checks that external callers cannot construct the sealed token. Mutation tests
must fail for actual behavioral assertions. These tests do not attest physical
boot, source acquisition, firmware compatibility or GPU operation.

# Source MTR membership and scoped thermal selection

The actual parser skips every polynomial payload and retains enabled ID
membership, bound to immutable copied bytes. The thermal adapter requires the
matching software contract and supplied identity; it does not qualify hardware
acquisition or thermal safety. Reviewed source: ee39c64a6b8dd82527e298009e4e4b597c899e0c
and 5ad4d4c1ce697786fc5afe0c343c2c713f65e3b8. All cases are invented.

```sh
python3 -B tools/asahi/g15-native-mtr/check.py --mutants --receipt /tmp/mtr.json
python3 -B tools/asahi/g15-native-mtr/check_thermal.py --cc gcc --mutants --receipt /tmp/thermal.json
```

Actual Rust ADT/MTR/thermal and C context/mask bodies execute in userspace;
firmware token services are modeled. Compiler-negative cases check borrowed
lifetimes; mutation controls must fail. No device I/O, physical qualification,
module load or GPU operation. No historical receipt is reused.

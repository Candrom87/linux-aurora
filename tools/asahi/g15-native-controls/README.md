# Native controller inputs

Compiles the actual source parser with actual ADT helpers and record/power
schemas. Invented metadata checks every required scalar, optional overrides,
exact source ownership, endian/order, five contiguous zone rows, malformed and
truncated inputs, plus compiled fault and borrow-escape controls. No device
access, native calibration, firmware admission or power operation occurs.

```sh
python3 -B tools/asahi/g15-native-controls/check.py --mutants --receipt /tmp/controls.json
```

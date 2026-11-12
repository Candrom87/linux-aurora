# Native ADT export metadata ownership

Runs the production C helper against modeled OF/resource/PFN services. Checks
unique exact owner, node availability, memory policy, shape, alignment, size,
refusal outputs and node-reference balance. Ordinary-user synthetic CPU data
only; it never maps RAM, reads a device or loads a module.

```sh
python3 -B tools/asahi/g15-native-source-owner/check.py --cc gcc --mutants --receipt /tmp/owner.json
```

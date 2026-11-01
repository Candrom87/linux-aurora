# M3 Max J514M companion snapshots

These patches capture experimental work in progress as of 2026-11-01. They are companion changes to the `wip/m3max-j514m` kernel branch. They are not a release or evidence of working hardware support. Review and test them before use; some diagnostics involve hardware and boot code.

- `patches/m1n1.patch`: against `omacom/m1n1` commit `bc79acc434e612ffcb95bfc7cb2fd7543afac5f2` (`integration/aurora`).
- `patches/omarchy-ane.patch`: against `joshuaswarren/omarchy-ane` commit `063ae8e0376ac8e8ca04880bed2a065ce1e321ff` (`main`).

To reconstruct a companion change, check out the specified upstream commit in its own repository, then run `git apply --check /path/to/patch` followed by `git apply /path/to/patch`. Each project needs its own fork and review before proposing a pull request to that project's upstream.

Machine-specific logs, boot images, firmware, and local research files are not included in this branch.

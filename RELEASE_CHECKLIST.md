# Release checklist — 1.3.5

## Package integrity

- [x] Source regression suite passes.
- [x] Installed-wheel regression suite passes.
- [x] Python source/benchmark scripts compile.
- [x] Wheel metadata/version/runtime version agree.
- [x] Wheel RECORD hashes verify.
- [x] Wheel/source release builds are deterministic.
- [x] Source ZIP CRC verifies.
- [x] No private keys, API secrets, or development-machine absolute paths are present in the public release.
- [x] `AUTHOR.md`, human guide, and LLM/agent guide are present in the repository and installed package.

## Repair behavior

- [x] Normal repair + undo roundtrip passes.
- [x] Safe-mode structural repair passes.
- [x] Streaming repair + automatic undo passes.
- [x] Multi-file materialization + rollback passes.
- [x] Malformed-input rejection is covered.
- [x] Forward replay and inverse roundtrip are covered.
- [x] Ambiguous/underdetermined cases abstain rather than mutate.
- [x] Four consecutive clean idempotence checks pass; the first zero is treated as warm-up and the following three are counted.

## Benchmark/reproducibility

- [x] Locked internal functional corpus is shipped with hashes.
- [x] Benchmark harness paths are repository-relative and portable.
- [x] Comparator dependencies are isolated in the optional `benchmark` extra.
- [x] Existing benchmark evidence is preserved with its measured-version labels.
- [ ] Independent external real-world corpus benchmark with third-party provenance.

## Publication

- [x] MIT license present.
- [x] `CITATION.cff` present for GitHub/Zenodo metadata assistance.
- [x] GitHub CI and manual trusted-publishing workflow present.
- [ ] GitHub repository created/pushed by the author.
- [ ] PyPI Trusted Publisher/environment configured by the author.
- [ ] Zenodo deposit/release created by the author.

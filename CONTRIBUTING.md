# Contributing

Changes should preserve the package's conservative repair contract:

1. A proposed edit must be deterministic and reversible.
2. The edit must be tested against the full configured consistency score before commit.
3. A committed repair must pass forward replay and inverse roundtrip verification.
4. Inferred relationships must remain diagnostic unless their stability contract passes.
5. New rule types must have explicit JSON schema validation and tests.
6. CSV values must remain data; no arbitrary expression or formula execution is allowed.

Run the test suite with:

```bash
python -m pytest -q
```

Build a release candidate with:

```bash
python -m build
python -m twine check dist/*
```

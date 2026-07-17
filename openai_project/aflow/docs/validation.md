# Validation and proof boundaries

The package-local suite separates proof groups:

- schema tests prove all 23 authoritative documents are byte-locked, meta-valid, and reference-resolvable;
- unit tests prove hashes, invariants, deterministic checks, substantiation, transitions, closure, locking, drift, traces, and final decisions;
- boundary tests trap subprocess, socket/network, and protected/analyzed-repository writes and inspect import boundaries;
- integration tests prove clean, blocking, closure, lock, and returned-evidence lifecycles;
- acceptance tests evaluate every isolated fixture and the exact nine-step demo;
- CLI tests prove successful and nonzero exit contracts.

These tests do not prove any external build. Docker, Edge, network services, and live model providers are deliberately outside the package and were not used.


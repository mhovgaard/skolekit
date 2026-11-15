"""Where a scan runs: the parent's machine or Google Cloud (phase 10).

- `scan`: one scan with every stage, check and output, around a runtime;
- `gcp`: the Cloud Run job's runtime, with its state in the private bucket;
- `local`: the parent's machine, with its state in the household's folder;
- `config`: `[runtime]`, `[weather]` and `[gcp]` in skoleklar.toml, and the
  one-writer guard;
- `schedule`: `skoleklar scan-schedule`, the local scan times as a launchd job or a systemd timer.
"""

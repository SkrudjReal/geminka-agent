# Runtime

Geminka uses the authenticated `agy` CLI directly. The bot does not start,
inspect, or authorize through Antigravity IDE, Xvfb, or a language server.

## Linux, WSL, and macOS

Install and authenticate `agy` once, then verify the CLI independently:

```bash
agy models
```

Set an explicit executable path only when `agy` is not on `PATH`:

```bash
AGY_CLI_PATH="$HOME/.local/bin/agy" ./run.sh
```

The default transport is `AGY_TRANSPORT=agy`. The old OpenAI-compatible OMP
bridge remains an opt-in legacy path only:

```bash
AGY_TRANSPORT=omp OMP_BASE_URL=http://127.0.0.1:4000/v1 ./run.sh
```

`run.sh` and the systemd service only launch the Python bot; no graphical
session or separate local server is required.

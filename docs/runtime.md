# Antigravity runtime

`run.sh` can bootstrap the Antigravity IDE language server on WSL and native
Linux. The IDE is required because the bundled `open-antigravity` gateway
discovers its authenticated local language-server process.

## WSL

Install Antigravity IDE on Windows and sign in once. The runner opens a hidden
WSL workspace through `powershell.exe` and closes only the process it started.

If discovery fails, point directly to the Windows executable:

```bash
ANTIGRAVITY_EXE='/mnt/c/Program Files/Antigravity/Antigravity IDE.exe' ./run.sh
```

## Native Linux

Install the native Antigravity IDE, sign in once, and install Xvfb for an
invisible graphical session:

```bash
# Debian/Ubuntu
sudo apt install xvfb

./run.sh
```

The runner finds `antigravity` or `antigravity-ide` in `PATH`. A custom binary
can be selected with `ANTIGRAVITY_EXE=/path/to/antigravity`.

Set `GEMINKA_ANTIGRAVITY_HEADLESS=false` to use the current graphical session
instead of Xvfb. Set `GEMINKA_AUTO_START_ANTIGRAVITY=false` to require an
already-running language server. By default, a process started by `run.sh` is
stopped with the bot; use `GEMINKA_STOP_ANTIGRAVITY_ON_EXIT=false` to keep it.

For a non-standard credentials database location, set:

```bash
ANTIGRAVITY_STATE_DB=/path/to/state.vscdb ./run.sh
```

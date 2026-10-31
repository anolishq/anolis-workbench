# anolis-workbench

The workbench is a desktop and browser app for an Anolis machine. You build
the machine's configuration in it, run that configuration on your own
computer, install it on the target host, and operate the running runtime. It
serves its interface at `http://127.0.0.1:3010`.

## Install

- **Desktop app** (Windows, macOS, x86_64 Linux): download the installer from
  [Releases](https://github.com/anolishq/anolis-workbench/releases/latest).
  It bundles its own Python and opens a window over the same local server. The
  command-line tools below are not part of it; install the Python package for
  those.
- **Python package** (Python 3.10 or later):

  ```sh
  pipx install anolis-workbench
  anolis-workbench
  ```

  Then open `http://127.0.0.1:3010`. Current Debian, Ubuntu and Homebrew
  Python refuse a system-wide `pip install`; `pipx` (`sudo apt install pipx`
  on Debian and Ubuntu) or a virtual environment avoids that.
- **Raspberry Pi**: install the same way. It opens no browser on the Pi; reach
  it from another computer over an SSH tunnel:

  ```sh
  ssh -L 3010:127.0.0.1:3010 <user>@<pi>
  ```

  To keep it running, install it as a service with
  `anolis-provision workbench-service`, which also lets Operate use the
  installed runtime's API token.

The workbench has no login. It listens on `127.0.0.1` unless `--host` or
`ANOLIS_WORKBENCH_HOST` sets another address, and anyone who can reach that
address can use it. Treat access to it as access to the account it runs as.

Projects are kept under `~/.anolis/systems/`, or under the directory
`ANOLIS_DATA_DIR` names.

## First start

On a machine where the runtime is installed (`/opt/anolis/manifest.json`), the
workbench imports the installed project, read-only, every time it starts, and
opens on it in Operate.

Anywhere else, the first start offers three ways in: **Set up this device**
(runs the install here), **Set up a remote device** (over SSH), or
**Import a project**; **Skip** goes to the project list. Home lists your
projects and holds **Create Project** (from a template) and
**Import Machine Profile**. Import takes a directory on the machine running
the workbench, or, with **Choose folder…**, a folder on the computer your
browser runs on, which the browser uploads (at most 200 files and 2 MiB).

## Compose, Commission, Operate

- **Compose** edits a project: a machine profile with the runtime
  configuration, providers, devices and behavior trees. Provider settings are
  edited in forms generated from each provider's published config schema.
  Imported projects are read-only here.
- **Commission** runs the project's runtime on this computer and checks it,
  exports a handoff package that records what was commissioned, and deploys
  the project to a target host. Running locally needs the runtime and provider
  binaries on this computer, named in the project's host paths: a template's
  host paths point at a source build, and `anolis-provision install` points
  them at the installed binaries under `/opt/anolis/bin`. Imported projects can
  be deployed but not run locally or exported.
- **Operate** drives a running runtime through its HTTP API: an operator panel
  with the machine's readings and Start/Stop experiment, and under
  Engineering, device state, function calls, mode and the software stop. It
  finds a runtime the workbench started, or the installed one on this machine.
  For a runtime on another machine, start the workbench with
  `ANOLIS_WORKBENCH_RUNTIME_URL` set to its address, and
  `ANOLIS_WORKBENCH_RUNTIME_TOKEN` to its API token when auth is on.

Deployment always goes through the runtime's `install.sh`; see
[Deployment](https://anolishq.github.io/repos/anolis/deploy/README). Deploying to
another host over SSH needs key-based login, the host's key already accepted
(SSH in once from a terminal), and passwordless `sudo` on the target; a sudo
password works only for this machine.

## Command line

| Command | Use |
| --- | --- |
| `anolis-workbench` | Start the workbench server |
| `anolis-provision` | Install on this machine (`install`), over SSH (`remote`), to several hosts (`fleet`), or build an offline bundle (`bundle`); install the workbench as a service (`workbench-service`, `--uninstall` to remove it); also `rollback`, `update` and `check-update` |
| `anolis-package` | Build a handoff package from a project |
| `anolis-validate` | Validate a handoff package |

`anolis-provision fleet --file fleet.yaml` reads a list of targets:

```yaml
defaults:            # optional; any target key below
  project: my-machine
targets:
  - name: rig-1      # required
    host: pi@10.0.0.21   # required, user@host
    project: my-machine  # workspace project to install; seeded from
    template: sim-quickstart  # this template if it does not exist yet
    key: ~/.ssh/id_ed25519
    install_prefix: /opt/anolis
```

## Reference

- [Commissioning handoff runbook](./commissioning-handoff-runbook.md)
- [Handoff package v1](./contracts/handoff-package-v1.md)

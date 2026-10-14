# Backconf

**Local, versioned backups for Linux.**

Backconf is a Linux backup utility designed to help users maintain local backup snapshots of important files and directories. It uses a background daemon to manage backup operations and a command-line interface (CLI) to inspect system health and backup status.

The project is designed for users who want a lightweight, self-hosted backup solution without relying on a cloud service.

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [System Service](#system-service)
- [File Locations](#file-locations)
- [Repair and Uninstallation](#repair-and-uninstallation)
- [Troubleshooting](#troubleshooting)
- [Security](#security)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)

## Overview

Backconf provides a foundation for managing local backups through a CLI and a background service.

The project separates user-facing commands from background operations:

- **CLI (`backconf.py`):** Provides commands for interacting with Backconf.
- **Core (`backconf_core.py`):** Contains shared backup functionality.
- **Daemon (`backconfd.py`):** Runs the background service.
- **Systemd unit (`backconfd.service`):** Defines how the daemon runs as a system service.
- **Configuration (`config.example.json`):** Provides an example configuration.
- **Installer (`install.sh`):** Installs and manages the application on the system.

## Features

- **Local backups:** Store backup data on local storage.
- **Snapshot-oriented design:** Organize backup data into snapshots for recovery workflows.
- **Background daemon:** Run backup management independently of an interactive terminal.
- **Command-line interface:** Inspect the installation and query backup status.
- **Health diagnostics:** Use the doctor command to check the health of the Backconf installation.
- **Centralized configuration:** Keep service settings in a dedicated configuration file.
- **Systemd integration:** Manage the daemon using standard Linux service tools.
- **Repair support:** Repair an existing installation using the installer.
- **Uninstallation support:** Remove the application through its installer.

> Note: The precise snapshot retention policies, scheduling capabilities, file-restoration commands, and integrity checks depend on the implemented version. Consult the source code and current configuration before relying on any particular recovery behavior.

## Architecture

```text
                  User / Administrator
                          |
                          v
                    Backconf CLI
                    backconf.py
                          |
                          v
                    Backconf Core
                 backconf_core.py
                          |
                          v
                    Backconf Daemon
                    backconfd.py
                          |
                          v
                 Local Snapshot Storage
              /var/lib/backconf/snapshots
```

The CLI and daemon have distinct responsibilities. The CLI provides an interface for users, while the daemon handles background work using the shared core functionality.

The actual communication mechanism and supported operations are defined by the implementation.

## Requirements

Backconf is intended for Linux systems with systemd.

Typical requirements include:

- Python 3
- Bash
- systemd
- Standard Linux filesystem utilities
- Appropriate permissions for the configured backup sources and destination
- `sudo` for installation operations that modify system-managed locations

Git is needed if you obtain the source using the Git repository.

## Installation

### Option 1: Clone the repository

```bash
git clone https://github.com/itzsevrus/backconf.git
cd backconf
```

Review the installer before executing it:

```bash
less install.sh
```

Then run the installer using the supported installation command:

```bash
sudo bash install.sh
```

The exact installer invocation depends on the argument interface supported by the version you have checked out. Consult `bash install.sh --help` if the script provides help, or inspect its argument parser before proceeding.

### Option 2: Using Backconf Setup

If you installed the separate `backconf-setup` package through Forge, its launcher may be used to start the installation:

```bash
backconf-setup install
```

This command belongs to the setup package, not the Backconf CLI itself. Review the setup launcher and Backconf installer before running them.

### Verify the installation

Check the service status:

```bash
systemctl is-active backconfd
```

Check Backconf's diagnostics:

```bash
backconf doctor
```

Inspect the configured backup targets:

```bash
backconf list
```

An installation with no configured backup targets can be healthy while still having no backups to protect.

## Configuration

Backconf uses the configuration file:

```text
/etc/backconf/config.json
```

An example configuration is provided in `config.example.json`.

Inspect the example before creating or editing your configuration:

```bash
cat config.example.json
```

The configuration determines the settings understood by the installed version of Backconf. Use the exact field names, data types, and structure supported by the source code.

### Configuration guidelines

1. Back up your current configuration before editing it.
2. Use valid JSON syntax.
3. Verify that each source path exists and is accessible.
4. Confirm that the snapshot destination has sufficient free space.
5. Check ownership and permissions for backup sources and storage.
6. Run `backconf doctor` after making changes.

Do not assume that a directory is protected merely because it appears in a configuration file. Verify that Backconf recognizes the target and that backup operations complete successfully.

## Usage

### Check system health

```bash
backconf doctor
```

The doctor command reports the health of the Backconf installation and relevant configuration and service state.

### List backup targets

```bash
backconf list
```

Use this command to inspect the backup targets recognized by Backconf.

If the output indicates that no targets are configured, configure valid targets using the schema and workflow supported by your installed version.

### Inspect daemon status

```bash
systemctl status backconfd
```

Check whether the daemon is active before investigating background backup operations.

### Inspect daemon logs

```bash
journalctl -u backconfd
```

To follow new log entries:

```bash
journalctl -u backconfd -f
```

These commands are useful for diagnosing startup failures, configuration errors, permission issues, and other service problems.

## System Service

Backconf provides a systemd service unit named `backconfd.service`.

Common service-management commands include:

```bash
sudo systemctl status backconfd
sudo systemctl restart backconfd
sudo systemctl stop backconfd
```

To inspect whether the service starts automatically:

```bash
systemctl is-enabled backconfd
```

Restarting the daemon does not, by itself, verify that a backup completed successfully. Always check the logs and the resulting snapshot data.

## File Locations

The standard installation layout used by Backconf is:

| Path | Purpose |
|---|---|
| `/usr/local/lib/backconf` | Application code |
| `/usr/local/bin/backconf` | CLI entry point |
| `/etc/backconf/config.json` | Main configuration |
| `/etc/systemd/system/backconfd.service` | systemd service unit |
| `/var/lib/backconf/snapshots` | Snapshot storage |
| `/run/backconf` | Runtime files |

The actual paths may differ if the installation was customized.

### Backup storage

Snapshots are stored under `/var/lib/backconf/snapshots` by default.

Keep in mind that a backup on the same physical disk as the original files does not protect against every failure. For stronger protection, maintain another copy on a separate disk or another trusted storage destination.

## Repair and Uninstallation

Backconf's installer supports maintenance operations in addition to installation.

Check the argument parser in your installed `install.sh` to identify the exact syntax supported by your version.

Before repairing an installation:

- Preserve your configuration.
- Preserve your snapshot directory.
- Review any installer changes that could overwrite existing files.
- Confirm that the installer will not delete backup data.

Before uninstalling:

- Identify where your snapshots are stored.
- Decide whether you want to retain or

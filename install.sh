#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

APP_DIR=/usr/local/lib/backconf
BIN=/usr/local/bin/backconf
UNIT=/etc/systemd/system/backconfd.service
CONFIG_DIR=/etc/backconf
DATA_DIR=/var/lib/backconf
RUNTIME_DIR=/run/backconf

log() {
    printf '[backconf-installer] %s\n' "$*"
}

die() {
    printf '[backconf-installer] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Backconf installer

Usage:
  ./install.sh                  Install or upgrade
  ./install.sh install          Install or upgrade
  ./install.sh repair           Repair files, permissions, and service
  ./install.sh uninstall        Uninstall Backconf
  ./install.sh --repair         Alias for repair
  ./install.sh --clean          Alias for uninstall
  ./install.sh --help           Show this help

Privileges:
  Requests sudo automatically when required.

Data safety:
  Installation and repair preserve existing configuration and snapshots.
  Uninstallation asks whether to preserve or delete backup data.
EOF
}

# ------------------------------------------------------------
# Parse commands.
# ------------------------------------------------------------

mode=install

if [[ $# -gt 1 ]]; then
    die "Only one command is accepted. Run ./install.sh --help."
fi

case "${1:-}" in
    "")
        mode=install
        ;;
    install|--install)
        mode=install
        ;;
    repair|--repair)
        mode=repair
        ;;
    uninstall|--clean)
        mode=clean
        ;;
    help|--help|-h)
        usage
        exit 0
        ;;
    *)
        usage >&2
        die "Unknown command: $1"
        ;;
esac

# ------------------------------------------------------------
# Request administrator privileges when necessary.
# Help does not require root.
# ------------------------------------------------------------

if [[ $EUID -ne 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
        die "sudo is not installed. Install sudo or run this script as root."
    fi

    log "Administrator privileges are required."
    exec sudo -- bash "$ROOT/install.sh" "${1:-}"
fi

if [[ "$mode" == repair ]]; then
    log "Starting repair..."
elif [[ "$mode" == clean ]]; then
    log "Starting uninstall..."
else
    log "Starting installation or upgrade..."
fi

# ------------------------------------------------------------
# Uninstallation.
# ------------------------------------------------------------

if [[ "$mode" == clean ]]; then
    echo
    echo "Backconf clean uninstall"
    echo

    # Remove only a recognized legacy Backconf service.
    if systemctl cat backconf.service >/dev/null 2>&1; then
        legacy="$(systemctl show -p ExecStart --value \
            backconf.service 2>/dev/null || true)"

        if [[ "$legacy" == *'/backconf'* ||
              "$legacy" == *'backconf --daemon'* ]]; then
            log "Removing obsolete Backconf service."
            systemctl disable --now backconf.service 2>/dev/null || true
            rm -f /etc/systemd/system/backconf.service
            rm -rf /etc/systemd/system/backconf.service.d
        else
            log "Leaving unrelated backconf.service untouched."
        fi
    fi

    # Stop the daemon before touching its data.
    systemctl disable --now backconfd.service 2>/dev/null || true

    echo
    echo "Choose backup data handling:"
    echo "  1) Preserve snapshots and state in another directory"
    echo "  2) Permanently delete all Backconf backup data"
    echo "  3) Cancel uninstallation"

    read -r -p 'Choice [1/2/3]: ' choice

    case "$choice" in
        1)
            invoking_user="${SUDO_USER:-}"

            if [[ -n "$invoking_user" ]] &&
               getent passwd "$invoking_user" >/dev/null; then
                home="$(getent passwd "$invoking_user" | cut -d: -f6)"
            else
                home=/root
            fi

            read -r -p \
                "Destination directory [$home/backconf-backups]: " dest

            dest="${dest:-$home/backconf-backups}"

            [[ "$dest" == /* ]] ||
                die "Destination must be an absolute path."

            mkdir -p -- "$dest"

            # Canonicalize the destination to reject unsafe locations.
            dest="$(realpath -m -- "$dest")"
            data_real="$(realpath -m -- "$DATA_DIR")"

            case "$dest/" in
                "$data_real/"*)
                    die "Cannot preserve data inside the original Backconf data directory."
                    ;;
            esac

            stamp="$(date -u +%Y%m%d-%H%M%S)"
            payload="$dest/backconf-data-$stamp"

            [[ ! -e "$payload" && ! -L "$payload" ]] ||
                die "Destination already exists: $payload"

            mkdir -m 0700 -- "$payload"

            if [[ -d "$DATA_DIR" ]]; then
                if ! cp -a -- "$DATA_DIR/." "$payload/"; then
                    echo "Copy failed. Original backup data has not been deleted." >&2
                    exit 1
                fi

                # Compare source and destination before allowing removal.
                if ! diff -qr -- "$DATA_DIR" "$payload"; then
                    echo "Verification failed. Original backup data has not been deleted." >&2
                    exit 1
                fi
            fi

            if [[ -n "$invoking_user" ]] &&
               id "$invoking_user" >/dev/null 2>&1; then
                chown -R "$invoking_user" "$payload"
            fi

            chmod 0700 "$payload"

            echo
            log "Backup data preserved and verified at:"
            printf '  %s\n' "$payload"
            ;;

        2)
            echo
            echo "WARNING: This permanently deletes Backconf snapshots and state."
            read -r -p \
                'Type DELETE to permanently delete all backup data: ' answer

            [[ "$answer" == DELETE ]] ||
                die "Confirmation did not match. Uninstallation cancelled."

            ;;

        *)
            echo "Uninstallation cancelled. Backup data and installation were left in place."
            echo "The daemon may remain stopped; check with systemctl status backconfd."
            exit 1
            ;;
    esac

    # Only remove Backconf-managed files and directories.
    systemctl disable --now backconfd.service 2>/dev/null || true

    rm -f -- "$UNIT" /etc/tmpfiles.d/backconf.conf
    rm -rf -- /etc/systemd/system/backconfd.service.d
    rm -rf -- "$APP_DIR" "$CONFIG_DIR" "$RUNTIME_DIR" "$DATA_DIR"
    rm -f -- "$BIN" /usr/bin/backconf

    systemctl daemon-reload

    if getent group backconf >/dev/null; then
        if ! groupdel backconf 2>/dev/null; then
            echo "Note: group backconf remains because it is still in use."
        fi
    fi

    log "Backconf application removed."
    exit 0
fi

# ------------------------------------------------------------
# Validate source files and dependencies.
# ------------------------------------------------------------

for file in \
    backconf.py \
    backconfd.py \
    backconf_core.py \
    backconfd.service \
    config.example.json
do
    [[ -f "$ROOT/$file" ]] ||
        die "Missing required project file: $file"
done

command -v python3 >/dev/null 2>&1 ||
    die "Python 3 is required."

python3 - <<'PY' ||
import sys
raise SystemExit(0 if sys.version_info >= (3, 9) else 1)
PY
    die "Python 3.9 or newer is required."

command -v systemctl >/dev/null 2>&1 ||
    die "systemctl is required for service management."

# ------------------------------------------------------------
# Stop the daemon before replacing application files.
# ------------------------------------------------------------

systemctl stop backconfd.service 2>/dev/null || true

# Remove only the known obsolete Backconf service.
if systemctl cat backconf.service >/dev/null 2>&1; then
    legacy="$(systemctl show -p ExecStart --value \
        backconf.service 2>/dev/null || true)"

    if [[ "$legacy" == *'backconf --daemon'* ]]; then
        systemctl disable --now backconf.service 2>/dev/null || true
        rm -f /etc/systemd/system/backconf.service
        rm -rf /etc/systemd/system/backconf.service.d
        log "Removed obsolete Backconf service definition."
    else
        log "Warning: unrelated or unrecognized backconf.service left untouched."
    fi
fi

# ------------------------------------------------------------
# Create required group and directories.
# ------------------------------------------------------------

getent group backconf >/dev/null ||
    groupadd --system backconf

install -d -o root -g root -m 0755 "$APP_DIR"
install -d -o root -g backconf -m 0750 \
    "$CONFIG_DIR" "$DATA_DIR" "$DATA_DIR/snapshots"

chown root:root "$APP_DIR"
chmod 0755 "$APP_DIR"

# ------------------------------------------------------------
# Install application files.
# ------------------------------------------------------------

for file in \
    backconf_core.py \
    backconf.py \
    backconfd.py \
    config.example.json
do
    src="$ROOT/$file"
    dst="$APP_DIR/$file"

    if [[ "$(readlink -f "$src")" != "$(readlink -m "$dst")" ]]; then
        install -o root -g root -m 0644 -- "$src" "$dst"
    fi
done

chmod 0644 \
    "$APP_DIR/backconf_core.py" \
    "$APP_DIR/backconf.py" \
    "$APP_DIR/backconfd.py" \
    "$APP_DIR/config.example.json"

if [[ "$(readlink -f "$ROOT/backconfd.service")" != \
      "$(readlink -m "$APP_DIR/backconfd.service")" ]]; then
    install -o root -g root -m 0644 \
        "$ROOT/backconfd.service" "$APP_DIR/backconfd.service"
fi

if [[ "$(readlink -f "$ROOT/install.sh")" != \
      "$(readlink -m "$APP_DIR/install.sh")" ]]; then
    install -o root -g root -m 0755 \
        "$ROOT/install.sh" "$APP_DIR/install.sh"
fi

# ------------------------------------------------------------
# Install the CLI wrapper.
# ------------------------------------------------------------

cat > "$BIN" <<'EOF'
#!/usr/bin/env python3
import runpy
import sys

sys.path.insert(0, "/usr/local/lib/backconf")
runpy.run_path(
    "/usr/local/lib/backconf/backconf.py",
    run_name="__main__",
)
EOF

chown root:root "$BIN"
chmod 0755 "$BIN"

# ------------------------------------------------------------
# Create configuration or safely migrate the existing config.
# Never overwrite a malformed existing config.
# ------------------------------------------------------------

if [[ ! -e "$CONFIG_DIR/config.json" ]]; then
    install -o root -g backconf -m 0640 \
        "$ROOT/config.example.json" "$CONFIG_DIR/config.json"
else
    backup="$CONFIG_DIR/config.json.bak.$(date -u +%Y%m%d%H%M%S)"

    cp -a -- "$CONFIG_DIR/config.json" "$backup"
    log "Existing configuration backed up to $backup"

    python3 - "$CONFIG_DIR/config.json" <<'PY'
import json
import os
import sys
import tempfile

path = sys.argv[1]

with open(path, "r", encoding="utf-8") as handle:
    raw = handle.read().strip()

# Repair only the known historical literal backslash-n suffix.
if raw.endswith("\\n"):
    raw = raw[:-2].rstrip()

try:
    obj = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(
        f"Existing configuration is invalid JSON; preserved unchanged: {exc}"
    )

if not isinstance(obj, dict):
    raise SystemExit(
        "Existing configuration is not a JSON object; preserved unchanged."
    )

if obj.get("schema") != 1:
    old_interval = obj.get("interval_seconds", 1800)
    targets = obj.get("targets", [])

    if (
        not isinstance(old_interval, int)
        or isinstance(old_interval, bool)
        or not 10 <= old_interval <= 365 * 86400
    ):
        raise SystemExit(
            "Existing interval is invalid; preserved unchanged."
        )

    if not isinstance(targets, list):
        raise SystemExit(
            "Existing targets are invalid; preserved unchanged."
        )

    obj = {
        "schema": 1,
        "settings": {
            "interval_seconds": old_interval,
            "watch_enabled": False,
        },
        "targets": targets,
    }

    directory = os.path.dirname(path)

    fd, temp_path = tempfile.mkstemp(
        dir=directory,
        prefix=".config-migrate-",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(obj, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.chmod(temp_path, 0o640)
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
PY

    chown root:backconf "$CONFIG_DIR/config.json"
    chmod 0640 "$CONFIG_DIR/config.json"
fi

# ------------------------------------------------------------
# Install service and correct managed permissions.
# ------------------------------------------------------------

install -o root -g root -m 0644 \
    "$ROOT/backconfd.service" "$UNIT"

chown -R root:backconf "$DATA_DIR" "$CONFIG_DIR"

chmod 0750 "$DATA_DIR" "$DATA_DIR/snapshots" "$CONFIG_DIR"
chmod 0640 "$CONFIG_DIR/config.json"

# Add the invoking non-root user to the Backconf group.
if [[ -n "${SUDO_USER:-}" &&
      "$SUDO_USER" != root ]] &&
   id "$SUDO_USER" >/dev/null 2>&1; then
    usermod -aG backconf "$SUDO_USER"
fi

# ------------------------------------------------------------
# Reload systemd and start the daemon.
# ------------------------------------------------------------

systemctl daemon-reload
systemctl enable --now backconfd.service

sleep 1

if ! systemctl is-active --quiet backconfd.service; then
    echo "Backconf service failed to start. Recent logs:" >&2
    journalctl -u backconfd.service -n 50 --no-pager >&2 || true
    exit 1
fi

# ------------------------------------------------------------
# Verify installation.
# ------------------------------------------------------------

if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
    if id -nG "$SUDO_USER" | tr ' ' '\n' | grep -qx backconf; then
        log "User $SUDO_USER belongs to the backconf group."
        echo "If this terminal predates the group change, log out and back in or run: newgrp backconf"
    else
        echo "WARNING: $SUDO_USER is not an active member of backconf." >&2
    fi
fi

if [[ ! -x "$BIN" ||
      ! -r "$APP_DIR/backconf.py" ||
      ! -r "$APP_DIR/backconf_core.py" ||
      ! -x "$APP_DIR" ]]; then
    die "Installation verification failed: CLI files are not accessible."
fi

log "Backconf $mode completed successfully."
echo "Application code is root-owned; configuration and backup data remain protected."
echo
echo "Verify with:"
echo "  backconf status"
echo "  backconf doctor"
echo "  backconf list"

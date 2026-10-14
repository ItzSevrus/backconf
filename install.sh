#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR=/usr/local/lib/backconf
BIN=/usr/local/bin/backconf
UNIT=/etc/systemd/system/backconfd.service
CONFIG_DIR=/etc/backconf
DATA_DIR=/var/lib/backconf
RUNTIME_DIR=/run/backconf
usage(){ cat <<'EOF'
Backconf installer
  sudo ./install.sh             Install or upgrade; preserve config and snapshots
  sudo ./install.sh --repair    Repair files, permissions, and service
  sudo ./install.sh --clean     Uninstall after choosing what to do with backup data
  ./install.sh --help           Show this help
EOF
}
mode=install
case "${1:-}" in '' ) ;; --repair) mode=repair;; --clean) mode=clean;; --help|-h) usage; exit 0;; *) echo "Unknown option: $1" >&2; usage >&2; exit 2;; esac
[[ $# -le 1 ]] || { echo 'Only one option is accepted.' >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo 'Run with sudo: sudo ./install.sh' >&2; exit 1; }
if [[ $mode == clean ]]; then
  echo 'Backconf clean uninstall'
  if systemctl cat backconf.service >/dev/null 2>&1; then
    echo 'Found legacy backconf.service. It will be stopped and disabled if it belongs to Backconf.'
    legacy=$(systemctl show -p ExecStart --value backconf.service 2>/dev/null || true)
    if [[ "$legacy" == *'/backconf'* || "$legacy" == *'backconf --daemon'* ]]; then
      systemctl disable --now backconf.service 2>/dev/null || true
      rm -f /etc/systemd/system/backconf.service
      rm -rf /etc/systemd/system/backconf.service.d
    else echo 'Leaving unrelated backconf.service untouched.'; fi
  fi
  systemctl disable --now backconfd.service 2>/dev/null || true
  echo 'Choose backup data handling:'
  echo '  1) Preserve snapshots and state in another directory'
  echo '  2) Permanently delete all Backconf backup data'
  echo '  3) Cancel'
  read -r -p 'Choice [1/2/3]: ' choice
  case "$choice" in
    1)
      invoking_user=${SUDO_USER:-}
      if [[ -n "$invoking_user" ]] && getent passwd "$invoking_user" >/dev/null; then home=$(getent passwd "$invoking_user" | cut -d: -f6); else home=/root; fi
      read -r -p "Destination directory [$home/backconf-backups]: " dest
      dest=${dest:-$home/backconf-backups}
      [[ "$dest" == /* ]] || { echo 'Destination must be an absolute path.' >&2; exit 1; }
      mkdir -p -- "$dest"
      stamp=$(date -u +%Y%m%d-%H%M%S); payload="$dest/backconf-data-$stamp"
      [[ ! -e "$payload" ]] || { echo "Destination already exists: $payload" >&2; exit 1; }
      mkdir -m 700 -- "$payload"
      if [[ -d "$DATA_DIR" ]]; then
        cp -a "$DATA_DIR/." "$payload/"
        if ! find "$payload" -mindepth 1 -print -quit | grep -q . && find "$DATA_DIR" -mindepth 1 -print -quit | grep -q .; then echo 'Data preservation verification failed.' >&2; rm -rf -- "$payload"; exit 1; fi
      fi
      if [[ -n "$invoking_user" ]] && id "$invoking_user" >/dev/null 2>&1; then chown -R "$invoking_user" "$payload"; fi
      chmod 700 "$payload"
      echo "Backup data preserved at: $payload"
      ;;
    2)
      read -r -p 'Type DELETE to permanently delete all snapshots and state: ' answer
      [[ "$answer" == DELETE ]] || { echo 'Cancelled; backup data was not deleted.'; exit 1; }
      ;;
    3|*) echo 'Cancelled; backup data and installation were left in place (daemon stopped).'; exit 1;;
  esac
  systemctl disable --now backconfd.service 2>/dev/null || true
  rm -f "$UNIT" /etc/tmpfiles.d/backconf.conf
  rm -rf /etc/systemd/system/backconfd.service.d "$APP_DIR" "$CONFIG_DIR" "$RUNTIME_DIR" "$DATA_DIR"
  rm -f "$BIN" /usr/bin/backconf
  systemctl daemon-reload
  if getent group backconf >/dev/null; then groupdel backconf 2>/dev/null || echo 'Note: group backconf remains because it is still in use.'; fi
  echo 'Backconf application removed.'; exit 0
fi
for f in backconf.py backconfd.py backconf_core.py backconfd.service; do [[ -f "$ROOT/$f" ]] || { echo "Missing required project file: $f" >&2; exit 1; }; done
command -v python3 >/dev/null || { echo 'Python 3 is required.' >&2; exit 1; }
python3 - <<'PY' || { echo 'Python 3.9 or newer is required.' >&2; exit 1; }
import sys
raise SystemExit(0 if sys.version_info >= (3,9) else 1)
PY
# Stop daemon before replacing code to prevent old code racing with new configuration.
systemctl stop backconfd.service 2>/dev/null || true
# Remove only the known broken legacy unit if it runs the obsolete CLI daemon mode.
if systemctl cat backconf.service >/dev/null 2>&1; then
  legacy=$(systemctl show -p ExecStart --value backconf.service 2>/dev/null || true)
  if [[ "$legacy" == *'backconf --daemon'* ]]; then
    systemctl disable --now backconf.service 2>/dev/null || true
    rm -f /etc/systemd/system/backconf.service
    rm -rf /etc/systemd/system/backconf.service.d
    echo 'Removed obsolete Backconf service definition.'
  else echo 'Warning: a backconf.service exists but does not match the known obsolete unit; leaving it untouched.'; fi
fi
getent group backconf >/dev/null || groupadd --system backconf
install -d -o root -g root -m 0755 "$APP_DIR"
install -d -o root -g backconf -m 0750 "$CONFIG_DIR" "$DATA_DIR" "$DATA_DIR/snapshots"
# CLI and Python modules must be readable by every local user; only root may modify them.
chown root:root "$APP_DIR"; chmod 0755 "$APP_DIR"
for file in backconf_core.py backconf.py backconfd.py config.example.json; do
  src="$ROOT/$file"; dst="$APP_DIR/$file"
  if [[ "$(readlink -f "$src")" != "$(readlink -m "$dst")" ]]; then install -o root -g root -m 0644 "$src" "$dst"; fi
done
chmod 0644 "$APP_DIR/backconf_core.py" "$APP_DIR/backconf.py" "$APP_DIR/backconfd.py"
chmod 0755 "$APP_DIR"
if [[ "$(readlink -f "$ROOT/backconfd.service")" != "$(readlink -m "$APP_DIR/backconfd.service")" ]]; then install -o root -g root -m 0644 "$ROOT/backconfd.service" "$APP_DIR/backconfd.service"; fi
if [[ "$(readlink -f "$ROOT/install.sh")" != "$(readlink -m "$APP_DIR/install.sh")" ]]; then install -o root -g root -m 0755 "$ROOT/install.sh" "$APP_DIR/install.sh"; fi
if [[ "$(readlink -f "$ROOT/backconf.py")" != "$(readlink -m "$BIN")" ]]; then install -o root -g root -m 0755 "$ROOT/backconf.py" "$BIN"; fi
# Ensure installed script imports sibling core module.
cat > "$BIN" <<'EOF'
#!/usr/bin/env python3
import runpy, sys
sys.path.insert(0, '/usr/local/lib/backconf')
runpy.run_path('/usr/local/lib/backconf/backconf.py', run_name='__main__')
EOF
chmod 0755 "$BIN"
if [[ ! -e "$CONFIG_DIR/config.json" ]]; then
  install -o root -g backconf -m 0640 "$ROOT/config.example.json" "$CONFIG_DIR/config.json"
else
  # Back up and migrate the legacy one-field prototype config only when safely parseable.
  cp -a "$CONFIG_DIR/config.json" "$CONFIG_DIR/config.json.bak.$(date -u +%Y%m%d%H%M%S)"
  python3 - "$CONFIG_DIR/config.json" <<'PY'
import json,sys,os,tempfile
p=sys.argv[1]
raw=open(p,'r',encoding='utf-8').read().strip()
# Repair only the known historical literal backslash-n suffix.
if raw.endswith('\\\\n'): raw=raw[:-2].rstrip()
obj=json.loads(raw)
if not isinstance(obj,dict): raise SystemExit('Existing config is not a JSON object; preserved it and refusing migration.')
if obj.get('schema') != 1:
    old_interval=obj.get('interval_seconds',1800)
    targets=obj.get('targets',[])
    if not isinstance(old_interval,int) or not 10 <= old_interval <= 365*86400: raise SystemExit('Existing interval is invalid; preserved config and refusing migration.')
    if not isinstance(targets,list): raise SystemExit('Existing targets are invalid; preserved config and refusing migration.')
    obj={'schema':1,'settings':{'interval_seconds':old_interval,'watch_enabled':False},'targets':targets}
    d=os.path.dirname(p); fd,tmp=tempfile.mkstemp(dir=d,prefix='.config-migrate-')
    with os.fdopen(fd,'w') as f: json.dump(obj,f,indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.chmod(tmp,0o640); os.replace(tmp,p)
PY
  chown root:backconf "$CONFIG_DIR/config.json"; chmod 0640 "$CONFIG_DIR/config.json"
fi
install -o root -g root -m 0644 "$ROOT/backconfd.service" "$UNIT"
# Ensure old data remains accessible to the root daemon and authorized group.
chown -R root:backconf "$DATA_DIR" "$CONFIG_DIR"
chmod 0750 "$DATA_DIR" "$DATA_DIR/snapshots" "$CONFIG_DIR"
chmod 0640 "$CONFIG_DIR/config.json"
# Add the invoking non-root user to the CLI-access group.
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]] && id "$SUDO_USER" >/dev/null 2>&1; then
  usermod -aG backconf "$SUDO_USER"
fi
systemctl daemon-reload
systemctl enable --now backconfd.service
sleep 1
if ! systemctl is-active --quiet backconfd.service; then
  echo 'Backconf service failed to start. Recent logs:' >&2
  journalctl -u backconfd.service -n 50 --no-pager >&2 || true
  exit 1
fi
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
  if id -nG "$SUDO_USER" | tr ' ' '\n' | grep -qx backconf; then
    echo "User $SUDO_USER is a member of backconf. If this terminal predates that membership, log out completely and back in (or run: newgrp backconf)."
  else
    echo "WARNING: $SUDO_USER is not an active member of backconf; socket access will fail until group membership is refreshed."
  fi
fi
# Verify the installed CLI and its modules are traversable/readable by non-root users.
if [[ ! -x "$BIN" || ! -r "$APP_DIR/backconf.py" || ! -r "$APP_DIR/backconf_core.py" || ! -x "$APP_DIR" ]]; then echo 'Install verification failed: CLI files are not accessible to ordinary users.' >&2; exit 1; fi
echo 'Backconf installed. Application code is root-owned and world-readable; daemon data remains protected.'
echo 'Verify with: backconf status; backconf doctor'

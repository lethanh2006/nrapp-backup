#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
root=/opt/nrapp/backup
case "${SSH_ORIGINAL_COMMAND:-}" in
  export)
    exec python3 "$root/current/scripts/backup.py" export
    ;;
  deploy\ *)
    read -r action revision extra <<<"$SSH_ORIGINAL_COMMAND"
    [[ "$action" == deploy && "$revision" =~ ^[0-9a-f]{40}$ && -z "${extra:-}" ]]
    exec 9>"$root/deploy.lock"
    flock -w 120 9
    incoming=$(mktemp "$root/incoming.XXXXXX.tar.gz")
    stage=$(mktemp -d "$root/releases/.stage.XXXXXX")
    trap 'rm -f -- "$incoming"; rm -rf -- "$stage"' EXIT
    timeout 120 head -c 1048577 >"$incoming"
    (( $(stat -c %s "$incoming") <= 1048576 ))
    python3 - "$incoming" "$stage" <<'PY'
import sys, tarfile
from pathlib import PurePosixPath
with tarfile.open(sys.argv[1], 'r:gz') as archive:
    members = archive.getmembers()
    if sum(m.size for m in members) > 2 * 1024 * 1024:
        raise SystemExit('Deployment payload too large')
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not (member.isfile() or member.isdir()):
            raise SystemExit('Invalid archive member')
    archive.extractall(sys.argv[2], members=members)
PY
    python3 -m py_compile "$stage/scripts/backup.py"
    bash -n "$stage/scripts/receiver.sh"
    [[ $(cat "$stage/REVISION") == "$revision" ]]
    exec 8>/opt/nrapp-backups/.backup.lock
    flock -w 2400 8
    if [[ -d "$root/releases/$revision" ]]; then
      rm -rf -- "$stage"
    else
      mv "$stage" "$root/releases/$revision"
    fi
    ln -s "releases/$revision" "$root/.current.tmp"
    mv -Tf "$root/.current.tmp" "$root/current"
    install -m 700 "$root/current/scripts/receiver.sh" "$root/receiver.next"
    mv -f "$root/receiver.next" "$root/receiver.sh"
    mkdir -p /home/deploy/.config/systemd/user
    install -m 600 "$root/current/systemd/"nrapp-backup.{service,timer} /home/deploy/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now nrapp-backup.timer
    printf 'DEPLOY_OK: %s\n' "$revision"
    ;;
  *) echo 'Rejected SSH command' >&2; exit 1 ;;
esac

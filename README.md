# NRApp Backup

This repository contains the encrypted backup worker, its restricted SSH
receiver, systemd units, and the GitHub Actions workflow for NRApp's VPS.
Backups are created on the VPS, encrypted with `age`, verified with SHA-256, and
then copied to GitHub as encrypted artifacts and repository files.

The backup worker does not run as a Docker service. The systemd user timer on the
VPS runs the worker against the existing NRApp containers.

## Backup contents

A successful encrypted archive can contain:

| Component | Source and format |
| --- | --- |
| MongoDB | Live per-collection dump of the database configured in the User service, `mongo.archive.gz` |
| Redis | `redis.rdb` created with `redis-cli --rdb` and checked with `redis-check-rdb` |
| RabbitMQ | `rabbitmq-definitions.json` containing exchanges, queues, bindings, and users; pending messages are not included |
| Payment PostgreSQL | Optional `payment.dump`, created only when the `payment-postgres` container is running |
| Backend configuration | Encrypted `backend-config.tar.gz` containing the backend env files, Compose files, and `docker/` deployment files |
| Metadata | `manifest.json` and `SHA256SUMS`, including the deployed backup revision and component checksums |

The archive does not include Cloudinary media, operating-system snapshots,
Prometheus/Grafana data, or a full VPS disk image. A database restore therefore
does not restore those resources.

## Backup flow

```text
systemd timer on VPS (19:30 UTC, about 02:30 Vietnam time)
  -> scripts/backup.py run
  -> MongoDB, Redis, RabbitMQ, and optional PostgreSQL export
  -> manifest + SHA-256 checksums
  -> age encryption
  -> /opt/nrapp-backups/nrapp-<timestamp>.tar.age

GitHub Actions (20:15 UTC, about 03:15 Vietnam time)
  -> restricted SSH command: export
  -> fetch the newest encrypted archive and checksum
  -> verify SHA-256
  -> upload a 30-day artifact
  -> commit the encrypted archive, checksum, and backups/LATEST
```

The export command creates a new backup when the newest archive is missing or
older than 23 hours. This protects the off-site job from a missed VPS timer but
does not replace the timer. The VPS keeps archives for up to 14 days and always
keeps at least seven copies when pruning succeeds. The intended recovery point is
about 24 hours when both schedules complete; recovery time must be measured in a
real restore exercise.

## Encryption and access control

`age` encrypts every payload before it leaves the backup worker. The decryption
key is created and kept on an administrator's recovery machine; it is not stored
in this repository, on the VPS, or in GitHub secrets. Keep an additional offline
copy of that key.

The `BACKUP_SSH_KEY` GitHub secret uses the pinned host key in
`deploy/known_hosts` and a forced command on the VPS. It accepts only `export` or
`deploy <40-character-commit-sha>`; it does not provide an interactive shell or
port forwarding. Archive paths, sizes, checksums, and deployment revisions are
validated before the active release is switched.

## Repository files

- `scripts/backup.py`: creates (`run`) and exports (`export`) encrypted backups.
- `scripts/receiver.sh`: forced-command receiver for export and exact-revision
  deployment.
- `systemd/nrapp-backup.service`: one-shot backup service.
- `systemd/nrapp-backup.timer`: daily timer with a five-minute random delay and
  `Persistent=true` catch-up behavior.
- `.github/workflows/backup.yml`: scheduled off-site export, verification,
  artifact upload, and encrypted archive commit.
- `.github/workflows/ci-cd.yml`: Python, shell, and systemd validation followed by
  exact-revision deployment of backup code on `main`.
- `VPS_BACKUP_OPERATIONS.md`: operational checks, manual runs, and recovery
  procedures.

## CI/CD behavior

Pull requests and pushes that change backup code run Python bytecode checks,
`bash -n`, ShellCheck, and systemd unit verification. Pushes containing only
`backups/**` are ignored by the deploy workflow. A successful `main` push deploys
only `scripts`, `systemd`, and a generated `REVISION` file to the VPS; the
receiver switches the active symlink after validating the archive and revision.

Required GitHub configuration:

- `BACKUP_SSH_KEY`: the restricted private key used by both workflows.
- `deploy/known_hosts`: the pinned VPS host key.

Run the same local checks with:

```bash
python3 -m py_compile scripts/backup.py
bash -n scripts/receiver.sh
shellcheck scripts/receiver.sh
systemd-analyze --user verify systemd/nrapp-backup.service systemd/nrapp-backup.timer
```

# Roadmap recovery

`recover_roadmap` restores an explicitly selected historical roadmap and preserves
newer report links. It does not infer whether a planned session happened.

## Before production recovery

1. Deploy the roadmap validation fix first, so the same input cannot reset the
   restored plan again. Do not run definition sync for this change.
2. Take a full Postgres backup using the [backup runbook](backup-and-recovery.md).
   Verify the archive with `pg_restore --list` and record its checksum and path.
3. Coordinate a short pause in writes to the affected case. Confirm its current
   active roadmap ID immediately before applying recovery.
4. Run the command without `--apply`, review its output, then use the same
   arguments with `--apply --backup-dir /persistent/private/backup/directory`.

The command additionally writes a scoped JSON backup with mode `0600`, flushes it
to disk and verifies SHA256 **before** database writes. It stops if the expected
active revision changed, if a report belongs to another case, or if an ambiguous
merge needs manual review. All database edits are transactional. Historical
roadmap revisions remain stored.

The scoped backup contains the source and current roadmap revisions and every
linked report, including private notes. Store it outside Git in a private,
persistent directory and retain its accompanying `.sha256` file. This is a
recovery-specific backup, not a replacement for a full database backup.

## Incident preview

The IDs below were observed during the investigation; refresh the expected
current ID before execution. Run inside the production backend environment:

```bash
python manage.py recover_roadmap \
  --visitor-id 116 \
  --doctor-id 14 \
  --case-id a567f23170174de6822a952889f27c77 \
  --source-entry-id 29183 \
  --expected-current-entry-id 30104
```

Expected plan for these revisions: 52 sessions, comprising the historical 51
sessions and the newer report `30101` appended as session 52. Its structured
report number changes from 1 to 52; report content remains intact. The original
16 completed sessions and 35 planned sessions remain unchanged. Appending the
newer report avoids assuming that it completed any particular planned session.
The earlier case is not modified.

Apply only after the full backup and preview are verified:

```bash
python manage.py recover_roadmap \
  --visitor-id 116 \
  --doctor-id 14 \
  --case-id a567f23170174de6822a952889f27c77 \
  --source-entry-id 29183 \
  --expected-current-entry-id 30104 \
  --backup-dir /persistent/private/vania-roadmap-backups \
  --apply
```

Record the new roadmap ID and backup filename printed by the command. Verify
the case canvas, the historical reports and the newer report after refreshing.

## Roll back a recovery

Use the JSON backup and new roadmap ID printed by the recovery command:

```bash
python manage.py rollback_roadmap_recovery \
  --backup-file /persistent/private/vania-roadmap-backups/ACTUAL_BACKUP.json \
  --expected-current-entry-id NEW_ROADMAP_ID
```

This previews rollback. Add `--apply` to restore the previously active roadmap
and original report numbers. The command verifies the backup checksum and
refuses to overwrite later edits to the roadmap or changed reports. It retains
all revisions and does not delete records. Rollback runs in a transaction.

Do not restore the whole production database just to undo this scoped recovery;
that could also overwrite unrelated work performed since the full backup.

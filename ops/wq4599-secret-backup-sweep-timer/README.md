# wq#4599 - secret-backup-sweep timer

Daily unit for `/home/lumen/bin/secret-backup-sweep` (lumen-bin). The tool classifies every secret in a backup
(`agent/.env.bak.*`, `.secrets/*.bak.*`, `.git-credentials.bak*`, gate-snapshot pre-images) and shreds a backup only
when each value is redundant, in use elsewhere, or proven dead by a probe that passed its controls.

- Default: report + aq rows (`ORPHAN_LIVE` = a credential found in a backup that authenticates but is in no live source).
- Shredding is armed by `~/.maint-ops/secret-backup-sweep.auto` (remove the file to disarm).
- Backups carry a 7d expiry: written by `gitea-rotate-finish` (lumen-bin) and `action_gate._stow` (this repo).
- `apply` refuses while a rotation journal (`~/.secrets/*inprogress*`) exists.
- Install: `lumen-run start --name sbs-install -- sudo bash ops/wq4599-secret-backup-sweep-timer/install.sh`; verify with `install.sh --verify`.

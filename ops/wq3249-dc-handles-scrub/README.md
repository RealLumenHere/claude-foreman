# wq#3249 dc-handles-scrub timer

Runs `/home/lumen/bin/dc-handles-scrub` every 10 minutes. It redacts credential-shaped strings in the Desktop
Commander handle store (`~/.dc-handles.db`) and checks the raw file bytes (freed pages, WAL).

Install: `lumen-run --root --name dcs-install -- bash ops/wq3249-dc-handles-scrub/install.sh`
Verify:  `bash ops/wq3249-dc-handles-scrub/install.sh --verify`
Remove:  `systemctl disable --now dc-handles-scrub.timer`

Limit: this bounds how long a secret stays in the store. It does not stop one reaching a chat transcript.

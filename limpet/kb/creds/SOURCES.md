# Credential knowledge-base sources (wq#4087)

Populates the `creds` category of the wq#4086 `limpet-kb` front end
(`limpet/kb.py`, `limpet/kb-sources.d/*.toml`): `defaultcreds` and
`ics-default-passwords` already had SHIP-tier catalog entries with no
data on disk yet ("populating /usr/share/limpet/kb/<category>/ with the
real artifacts is a drop-in/update follow-up" -- DESIGN-DECISIONS.md
wq#4086 D-note); `many-passwords.toml` is this ticket's new drop-in.
Each `.csv` ships with its upstream `.LICENSE` file alongside it so the
license travels with the data in the installed package
(`/usr/share/limpet/kb/creds/`). Fetched 2026-10-17.

| file | upstream | license | rows (data, excl. header) |
| --- | --- | --- | --- |
| `DefaultCreds-Cheat-Sheet.csv` | github.com/ihebski/DefaultCreds-cheat-sheet (`main`, `DefaultCreds-Cheat-Sheet.csv`) | MIT | 3767 |
| `many-passwords.csv` | github.com/many-passwords/many-passwords (`main`, `passwords.csv`) | GPL-2.0 | 2860 |
| `ics-default-passwords.csv` | github.com/danielmiessler/SecLists (`master`, `Passwords/Default-Credentials/scada-pass.csv`, upstream SCADA StrangeLove content), **reshaped** | MIT (SecLists repo license) | 107 |

The ticket's reference counts (3711 / 2859) are close but not exact
matches to the rows fetched above -- each upstream list is a live repo
that gained/lost rows between when `re-knowledge-bases.md` was written
and this fetch. `many-passwords` is within 1 row of its own published
stat ("Number of collected default passwords: 2860"), confirming it is
the right source.

**ics-default-passwords reshaping.** `kb-sources.d/ics-default-passwords.toml`
landed (wq#4086, ahead of any data) naming `arnaudsoullie/ics-default-passwords`
and declaring columns `vendor,product,username,password,protocol`. That repo
is a 6-row, no-LICENSE stub with different column names -- too thin and
unlicensed to ship. SecLists' `scada-pass.csv` (MIT via the SecLists repo
license, upstream SCADA StrangeLove content, 107 rows) is the richer,
clearly-licensed alternative, but its native schema is
`Vendor,Device,Default password,Port,Device type,Protocol,Source` with
username:password combined in one field. Reshaped it to the TOML's declared
header (splitting `Default password` on the first `:`) rather than editing
the already-landed TOML, to keep this ticket's diff additive. Every value in
the reshaped file is verbatim from the source row, just recolumned.

`many-passwords.csv` is GPL-2.0: `limpet-kb` only *reads* this data at
index/query time (no linking), so redistributing it alongside the GPL
text in `many-passwords.LICENSE` satisfies the license's source-
availability term for this file.

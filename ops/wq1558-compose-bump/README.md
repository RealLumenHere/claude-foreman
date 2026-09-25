# wq#1558 — compose_image_bump applier for compose-managed service_change plans

Found closing wq#1507/#1514: minerva-factory yellow `service_change` plans were
LLM drafts whose `apply_cmd` rebuilt a `docker run` from `docker inspect`
(plan `741a2aeb6819` glance, `a6fef713ac123` actual-budget,
"inspect-and-recreate"). Both `/home/lumen/glance` and `/home/lumen/actual-budget`
are compose-managed with digest-pinned images (wq#856), so that apply would:

- drift the running container away from compose — the next `compose up`
  recreates the OLD image;
- drop the digest pin;
- get `CAPABILITY_DENIED` in an fworker anyway (wq#1507 block note).

It also proposed an unvalidated tag (`v26.9.0` vs. the real `26.9.0`, pull
failed) — registry validation was happening at APPLY time or not at all.

## Fix

`compose_image_bump.py`:

- `resolve_compose_project(container)` — reads the container's own
  `com.docker.compose.project.working_dir` / `.service` labels instead of a
  guessed path.
- `validate_tag_exists(repo, tag)` — registry HEAD/manifest check, meant to be
  called during **plan** construction so a bad tag never reaches apply.
- `bump(...)` — edits only the compose file's `image:` line, keeps the digest
  pin (re-pins to the digest actually pulled), runs `docker compose up -d
  <service>`, health-probes, reverts the compose file and redeploys the old
  image on any failure, and takes an optional data backup first.
- `route_service_change_plan(plan)` — the seam a plan builder calls: for a
  compose-managed `target_container` it swaps the LLM-drafted `docker run`
  `apply_cmd` for an invocation of this applier; non-compose targets pass
  through untouched.

Reference shell prototype: `/home/lumen/uplift/compose-bump/bump.sh`.

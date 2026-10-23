# Workspaces

A **workspace** is one investigation: one target device or image, with all its dumps,
extractions, notes, and tool output kept together and kept apart from every other
investigation. The Target Device Wizard creates one for each target; you switch
between them as you work on more than one device.

## Why they are isolated

The devices and images you analyze are **untrusted from the first moment**. A plugged-in
gadget and a dropped firmware blob are both potentially hostile. Workspaces enforce a
hygiene that assumes so:

- The target artifact (image, dump, filesystem) lives under a `target/` directory
  inside the workspace.
- Tools are run against that artifact **by path argument** — Limpet never executes a
  program from inside the artifact directory, so a planted binary or a malicious
  `__init__.py` next to the data is never on the run path.
- Each workspace is its own directory tree, so output from one investigation cannot
  bleed into another.

## Working with workspaces

From the bottom terminal (or over SSH):

```sh
limpet workspace current        # which workspace am I acting in?
limpet workspace list           # all workspaces
```

The agent and the status line both read `limpet workspace current`, so the top of the
screen always tells you which investigation your next command affects.

## Where output goes

Large tool output — a flash dump, a decompilation, a packet capture — is written into
the active workspace and referenced from the chat rather than inlined. When the agent
says it extracted a filesystem, the files are in the workspace's `target/` tree, ready
for the next tool. This is also what keeps the chat readable: you see a path and a
short preview, not megabytes of hex.

!!! tip "One target, one workspace"
    Resist the urge to analyze two devices in one workspace. The isolation is the
    point — if something in one investigation turns out to be hostile, a clean
    boundary means it is contained to that workspace.

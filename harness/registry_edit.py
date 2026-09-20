"""registry_edit.py — comment-preserving edits to research/inference_nodes.yaml.
research/INFERENCE_NODE_REGISTRY.md §5; wq#2253 (C4 of wq#2241).

The registry carries seed provenance in its comments, so `yaml.safe_dump` would destroy the file's
audit trail. `fleet-node add|retire` therefore edit lines: set one scalar on a node row or on one of
its model rows (keeping a trailing `# comment`), or append a whole node block at end of file.
Callers re-parse the edited text with `yaml.safe_load` before trusting it.
"""
import json
import re

RESERVED = {"null", "true", "false", "yes", "no", "on", "off", "~"}
_PLAIN = re.compile(r"^[A-Za-z0-9_./:-]+$")
_NUMERIC = re.compile(r"^[-+]?[0-9]+(\.[0-9]+)?$")


class EditError(Exception):
    pass


def fmt(v):
    """YAML scalar text for `v`; dates and identifiers stay plain like the hand-written rows."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    if _PLAIN.match(s) and s.lower() not in RESERVED and not _NUMERIC.match(s):
        return s
    return json.dumps(s)


def _indent(line):
    return len(line) - len(line.lstrip(" "))


def _significant(line):
    return bool(line.strip()) and not line.lstrip().startswith("#")


def _block_end(lines, start, indent):
    """Index one past the block opened at `start`: it ends at the next real line dedented to `indent`."""
    i = start + 1
    while i < len(lines) and not (_significant(lines[i]) and _indent(lines[i]) <= indent):
        i += 1
    return i


def _node_span(lines, node):
    try:
        top = next(i for i, ln in enumerate(lines) if ln.rstrip() == "nodes:")
    except StopIteration:
        raise EditError("registry has no top-level `nodes:`") from None
    head = re.compile(rf"^  {re.escape(node)}:\s*(#.*)?$")
    for i in range(top + 1, len(lines)):
        if head.match(lines[i]):
            return i, _block_end(lines, i, 2)
    raise EditError(f"node {node!r} is not in the registry")


def _model_span(lines, lo, hi, family):
    item = re.compile(rf"^      - family:\s*{re.escape(family)}\s*(#.*)?$")
    for i in range(lo, hi):
        if item.match(lines[i]):
            return i, min(hi, _block_end(lines, i, 6))
    raise EditError(f"no model row for family {family!r}")


def set_field(text, node, key, value, family=None):
    """`text` with `key: value` set on `node` (or on the `family` model row of `node`). An existing
    line keeps its trailing comment; a missing key is inserted right under the row header."""
    lines = text.split("\n")
    lo, hi = _node_span(lines, node)
    indent = 4
    if family is not None:
        lo, hi = _model_span(lines, lo, hi, family)
        indent = 8
    pat = re.compile(rf"^( {{{indent}}}{re.escape(key)}:\s*)([^#\n]*?)(\s+#.*)?$")
    for i in range(lo + 1, hi):
        m = pat.match(lines[i])
        if m:
            lines[i] = f"{m.group(1).rstrip()} {fmt(value)}{m.group(3) or ''}"
            return "\n".join(lines)
    lines.insert(lo + 1, f"{' ' * indent}{key}: {fmt(value)}")
    return "\n".join(lines)


def get_field(text, node, key, family=None):
    """The raw scalar text of `key` on the row, or None. For reading back what set_field wrote."""
    lines = text.split("\n")
    lo, hi = _node_span(lines, node)
    indent = 4
    if family is not None:
        lo, hi = _model_span(lines, lo, hi, family)
        indent = 8
    pat = re.compile(rf"^ {{{indent}}}{re.escape(key)}:\s*([^#\n]*?)\s*(#.*)?$")
    for i in range(lo + 1, hi):
        m = pat.match(lines[i])
        if m:
            return m.group(1)
    return None


def append_node(text, name, fields, model):
    """`text` with a new node block at end of file. `fields` is an ordered dict of node scalars, `model`
    an ordered dict of the first model row's scalars (`family` first)."""
    if any(ln.startswith(f"  {name}:") for ln in text.split("\n")):
        raise EditError(f"node {name!r} already in the registry")
    out = [f"  {name}:"] + [f"    {k}: {fmt(v)}" for k, v in fields.items()]
    out += ["    wake:", "      method: plug-ac", "      measured_ready_s: null", "      last_measured: null",
            "    models:"]
    keys = list(model)
    out.append(f"      - {keys[0]}: {fmt(model[keys[0]])}")
    out += [f"        {k}: {fmt(model[k])}" for k in keys[1:]]
    out.append("        capabilities: {}")
    return text.rstrip("\n") + "\n\n" + "\n".join(out) + "\n"

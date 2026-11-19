"""wq#5015: lossless tool_result compression as a LiteLLM pre-call hook.

Filter patterns extracted from RTK/9router-go (wq#4978); the tool itself is rejected. RTK's
own filters are mostly LOSSY (git diff -> "+142/-89, 3 files"); every filter here is a
re-encoding instead, and each reversible one is proven at runtime: the hook keeps an encoding
only if decode(encode(x)) == x and the result is strictly shorter. Anything else - a parse
mismatch, an exception, growth - keeps the original text byte for byte.

Filters (detected from the content, first matching wins):
  read-numbered  `     12\tcode` / `    12→code` listings: drop the number padding; with
                 TOOL_COMPRESS_READ_NUMBERS=drop, drop consecutive numbers behind a legend
  grep           `path:line:text` runs: one path header per file, matches indented under it
  paths          find / ls -1 / glob / search-list output: one dir header per run of siblings
  tree           `tree` output: 4-column box-drawing prefixes -> 2-column ASCII
  git-diff       drop each `diff --git` header line that the ---/+++ lines already determine
  git-status     long-form status: drop git's fixed `(use "git ...")` hint lines (no state)
  dedup-log      runs of >= 3 identical consecutive lines -> one line + a repeat count
  smart-truncate LOSSY, off unless TOOL_COMPRESS_TRUNCATE_CHARS is set: keep head + tail

Every compressed result starts with one short legend line, `[rtk:<filter> ...]`, so the
model can read the format. The output is a pure function of the input, so an unchanged tool
result compresses identically on every turn and provider prompt caches stay warm.

Kill switch: TOOL_COMPRESS_DISABLE=1 in the gateway env. Per request: metadata
{"tool_compress": false}. Only tool results of >= TOOL_COMPRESS_MIN_CHARS (512) are touched.
"""
import os
import re

MIN_CHARS = int(os.environ.get("TOOL_COMPRESS_MIN_CHARS", "512"))
STATS = {"seen": 0, "compressed": 0, "chars_in": 0, "chars_out": 0, "by_filter": {}}


def _legend(name, text):
    return f"[rtk:{name} {text}]\n"


def _strip_legend(s, name):
    first, _, rest = s.partition("\n")
    if not first.startswith(f"[rtk:{name} "):
        raise ValueError("legend")
    return rest


# --- read-numbered -----------------------------------------------------------------------
_NUM_RE = re.compile(r"^( *)(\d+)(\t|→)(.*)$", re.S)


def _read_drop_on():
    return os.environ.get("TOOL_COMPRESS_READ_NUMBERS", "keep") == "drop"


def enc_read_numbered(s):
    lines = s.split("\n")
    parsed = []
    for ln in lines:
        m = _NUM_RE.match(ln)
        if not m:
            return None
        parsed.append(m)
    if len(parsed) < 3 or len({m.group(3) for m in parsed}) != 1:
        return None
    sep = parsed[0].group(3)
    nums = [int(m.group(2)) for m in parsed]
    widths = {len(m.group(1)) + len(m.group(2)) for m in parsed}
    if _read_drop_on() and not any(m.group(1) for m in parsed) and \
            nums == list(range(nums[0], nums[0] + len(nums))) and \
            all(m.group(2) == str(n) for m, n in zip(parsed, nums)):
        # opt-in: consecutive numbers are implied by the legend (lossless, but the model
        # loses per-line numbers it may cite or edit by)
        return (_legend("read-numbered", f"lines {nums[0]}-{nums[-1]} sep={'tab' if sep == chr(9) else 'arrow'} numbers omitted")
                + "\n".join(m.group(4) for m in parsed))
    if len(widths) != 1 or not any(m.group(1) for m in parsed):
        return None  # nothing padded to strip
    width = widths.pop()
    body = "\n".join(f"{m.group(2)}{sep}{m.group(4)}" for m in parsed)
    return _legend("read-numbered", f"w={width} line numbers unpadded") + body


def dec_read_numbered(s):
    first = s.split("\n", 1)[0]
    rest = _strip_legend(s, "read-numbered")
    dm = re.search(r"lines (\d+)-(\d+) sep=(tab|arrow) numbers omitted", first)
    if dm:
        start, sep = int(dm.group(1)), ("\t" if dm.group(3) == "tab" else "→")
        return "\n".join(f"{start + i}{sep}{ln}" for i, ln in enumerate(rest.split("\n")))
    width = int(re.search(r"w=(\d+)", first).group(1))
    out = []
    for ln in rest.split("\n"):
        m = re.match(r"^(\d+)(\t|→)(.*)$", ln, re.S)
        out.append(m.group(1).rjust(width) + m.group(2) + m.group(3))
    return "\n".join(out)


# --- grep --------------------------------------------------------------------------------
_GREP_RE = re.compile(r"^([^\s:][^:\n]*?):(\d+)([:-])(.*)$")


def enc_grep(s):
    lines = s.split("\n")
    if len(lines) < 4:
        return None
    out, cur = [], None
    for ln in lines:
        m = _GREP_RE.match(ln)
        if not m:
            return None
        path, num, sep, text = m.groups()
        if path != cur:
            out.append(path)
            cur = path
        out.append(f"  {num}{sep}{text}")
    if len(out) - len(lines) > len(lines) // 2:  # mostly one hit per file: no gain
        return None
    return _legend("grep", "matches grouped under their file; '  N:text'") + "\n".join(out)


def dec_grep(s):
    out, cur = [], None
    for ln in _strip_legend(s, "grep").split("\n"):
        if ln.startswith("  "):
            out.append(f"{cur}:{ln[2:]}")
        else:
            cur = ln
    return "\n".join(out)


# --- paths (find / ls -1 / glob / search-list) -------------------------------------------
_PATH_RE = re.compile(r"^[^\s].*$")


def _looks_like_paths(lines):
    if len(lines) < 5:
        return False
    slashy = sum(1 for ln in lines if "/" in ln)
    return all(_PATH_RE.match(ln) and len(ln) < 400 and ": " not in ln for ln in lines) \
        and slashy >= 0.8 * len(lines)


def enc_paths(s):
    lines = s.split("\n")
    if not _looks_like_paths(lines):
        return None
    out, i = [], 0
    while i < len(lines):
        d = lines[i].rpartition("/")[0]
        j = i
        while j < len(lines) and "/" in lines[j] and lines[j].rpartition("/")[0] == d \
                and lines[j].rpartition("/")[2]:
            j += 1
        if j - i >= 2 and d:
            out.append(d + "/")
            out += ["  " + lines[k].rpartition("/")[2] for k in range(i, j)]
            i = j
        else:
            out.append(lines[i])
            i += 1
    return _legend("paths", "'dir/' then its entries indented") + "\n".join(out)


def dec_paths(s):
    lines = _strip_legend(s, "paths").split("\n")
    out, cur = [], None
    for idx, ln in enumerate(lines):
        if ln.startswith("  ") and cur is not None:
            out.append(cur + ln[2:])
        elif ln.endswith("/") and idx + 1 < len(lines) and lines[idx + 1].startswith("  "):
            cur = ln
        else:
            cur = None
            out.append(ln)
    return "\n".join(out)


# --- tree --------------------------------------------------------------------------------
_TREE_UNITS = {"│   ": "| ", "    ": "  ", "├── ": "+ ", "└── ": "` "}
_TREE_BACK = {v: k for k, v in _TREE_UNITS.items()}


def enc_tree(s):
    lines = s.split("\n")
    if sum(1 for ln in lines if "├── " in ln or "└── " in ln) < 3:
        return None
    out = []
    for ln in lines:
        i, pre = 0, []
        while ln[i:i + 4] in _TREE_UNITS:
            pre.append(_TREE_UNITS[ln[i:i + 4]])
            i += 4
        rest = ln[i:]
        if pre and rest[:1] in ("|", "+", "`", " "):
            return None  # a name that starts like a unit would decode ambiguously
        if not pre and rest[:2] in _TREE_BACK:
            return None
        out.append("".join(pre) + rest)
    return _legend("tree", "'+ ' branch, '` ' last, '| ' pipe") + "\n".join(out)


def dec_tree(s):
    out = []
    for ln in _strip_legend(s, "tree").split("\n"):
        i, pre = 0, []
        while ln[i:i + 2] in _TREE_BACK:
            pre.append(_TREE_BACK[ln[i:i + 2]])
            i += 2
        out.append("".join(pre) + ln[i:])
    return "\n".join(out)


# --- git diff ----------------------------------------------------------------------------
_DIFF_HDR = re.compile(r"^diff --git a/(\S+) b/(\S+)$")


def enc_git_diff(s):
    lines = s.split("\n")
    if not lines or not _DIFF_HDR.match(lines[0]):
        return None
    out, dropped, i = [], 0, 0
    while i < len(lines):
        m = _DIFF_HDR.match(lines[i])
        if m and m.group(1) == m.group(2):
            # derivable only if a later '--- a/X' or '+++ b/X' in this file block names X
            j = i + 1
            while j < len(lines) and not lines[j].startswith("diff --git ") \
                    and not lines[j].startswith("@@"):
                j += 1
            block = lines[i + 1:j]
            p = m.group(1)
            if (f"--- a/{p}" in block or f"+++ b/{p}" in block) and \
                    not any(x.startswith("rename ") or x.startswith("copy ") for x in block):
                out.append("§")
                dropped += 1
                i += 1
                continue
        out.append(lines[i])
        i += 1
    if not dropped or any(ln == "§" for ln in lines):
        return None
    return _legend("git-diff", "'§' = diff --git header implied by ---/+++") + "\n".join(out)


def dec_git_diff(s):
    lines = _strip_legend(s, "git-diff").split("\n")
    out = []
    for i, ln in enumerate(lines):
        if ln == "§":
            j = i + 1
            p = None
            while j < len(lines):
                if lines[j].startswith("--- a/"):
                    p = lines[j][6:]
                    break
                if lines[j].startswith("+++ b/"):
                    p = lines[j][6:]
                    break
                j += 1
            out.append(f"diff --git a/{p} b/{p}")
        else:
            out.append(ln)
    return "\n".join(out)


# --- git status --------------------------------------------------------------------------
_HINT = re.compile(r'^  \(use "git [^"]+"( or "git [^"]+")*( to [^)]*)?\)$')


def enc_git_status(s):
    if not re.search(r"^(On branch |HEAD detached |Changes |Untracked files:)", s, re.M):
        return None
    lines = s.split("\n")
    kept = [ln for ln in lines if not _HINT.match(ln)]
    if len(kept) == len(lines):
        return None
    return _legend("git-status", "git's '(use \"git ...\")' hint lines removed") + "\n".join(kept)


def status_facts(s):
    """The repo state a git status carries: everything except the fixed hint lines."""
    return [ln for ln in s.split("\n") if not _HINT.match(ln) and not ln.startswith("[rtk:")]


# --- dedup-log ---------------------------------------------------------------------------
_REP = re.compile(r"^(.*) ⟨×(\d+)⟩$", re.S)


def enc_dedup(s):
    lines = s.split("\n")
    if any(_REP.match(ln) for ln in lines):
        return None
    out, i, hit = [], 0, False
    while i < len(lines):
        j = i
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        n = j - i
        if n >= 3:
            out.append(f"{lines[i]} ⟨×{n}⟩")
            hit = True
        else:
            out += lines[i:j]
        i = j
    if not hit:
        return None
    return _legend("dedup-log", "'line ⟨×N⟩' = line repeated N times") + "\n".join(out)


def dec_dedup(s):
    out = []
    for ln in _strip_legend(s, "dedup-log").split("\n"):
        m = _REP.match(ln)
        out += [m.group(1)] * int(m.group(2)) if m else [ln]
    return "\n".join(out)


# --- smart-truncate (LOSSY, opt-in) ------------------------------------------------------
def enc_truncate(s):
    cap = int(os.environ.get("TOOL_COMPRESS_TRUNCATE_CHARS", "0") or 0)
    if cap <= 0 or len(s) <= cap:
        return None
    head, tail = s[: cap * 2 // 3], s[-(cap // 3):]
    return (_legend("smart-truncate", f"LOSSY: {len(s) - len(head) - len(tail)} chars elided")
            + head + "\n[...]\n" + tail)


# Order matters: the most specific shapes first.
FILTERS = [
    ("read-numbered", enc_read_numbered, dec_read_numbered),
    ("git-diff", enc_git_diff, dec_git_diff),
    ("git-status", enc_git_status, None),
    ("tree", enc_tree, dec_tree),
    ("grep", enc_grep, dec_grep),
    ("paths", enc_paths, dec_paths),
    ("dedup-log", enc_dedup, dec_dedup),
]


def _verified(name, enc, dec, s):
    try:
        out = enc(s)
        if out is None or len(out) >= len(s):
            return None
        if dec is not None:
            if dec(out) != s:
                return None
        elif name == "git-status" and status_facts(out) != status_facts(s):
            return None
        return out
    except Exception:
        return None


def compress_text(s):
    """(text, filter_name or None). Never raises; never returns something longer."""
    if not isinstance(s, str) or len(s) < MIN_CHARS:
        return s, None
    for name, enc, dec in FILTERS:
        out = _verified(name, enc, dec, s)
        if out is not None:
            return out, name
    # the lossy last resort, only when TOOL_COMPRESS_TRUNCATE_CHARS is set
    out = None
    try:
        out = enc_truncate(s)
    except Exception:
        out = None
    if out is not None and len(out) < len(s):
        return out, "smart-truncate"
    return s, None


def _record(before, after, name):
    STATS["seen"] += 1
    STATS["chars_in"] += len(before)
    STATS["chars_out"] += len(after)
    if name:
        STATS["compressed"] += 1
        STATS["by_filter"][name] = STATS["by_filter"].get(name, 0) + 1


def _compress_content(content):
    """content of a tool message / tool_result block: str or list of {type:text} parts."""
    if isinstance(content, str):
        out, name = compress_text(content)
        _record(content, out, name)
        return out
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and \
                    isinstance(part.get("text"), str):
                out, name = compress_text(part["text"])
                _record(part["text"], out, name)
                part["text"] = out
    return content


def compress_messages(messages):
    """In place. OpenAI shape: role=tool messages. Anthropic shape: tool_result blocks."""
    if not isinstance(messages, list):
        return messages
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            msg["content"] = _compress_content(msg.get("content"))
        elif isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    block["content"] = _compress_content(block.get("content"))
    return messages


def should_run(data):
    if os.environ.get("TOOL_COMPRESS_DISABLE") == "1":
        return False
    md = (data or {}).get("metadata") or {}
    return md.get("tool_compress") is not False


try:  # inside the LiteLLM container
    from litellm.integrations.custom_logger import CustomLogger
except Exception:  # tests / measurement on the host
    class CustomLogger:  # type: ignore[no-redef]
        pass


class ToolResultCompressor(CustomLogger):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        try:
            if should_run(data):
                compress_messages(data.get("messages"))
        except Exception:
            pass  # fail-safe: never block a request over compression
        return data


proxy_handler_instance = ToolResultCompressor()

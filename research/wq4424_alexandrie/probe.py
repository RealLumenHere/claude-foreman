"""wq#4424 probe: does Alexandrie's markdown importer keep fleet-shaped notes intact?

Line-for-line port of extractMetadataFromMarkdown/parseTags from
vendored_node_helpers.ts (frontend/app/helpers/node.ts:67-99 at
Smaug6739/Alexandrie@32a8c76da1380637cbaec14455761bacde50b6d3). Node is not on
the worker allowlist, so the TS can't run here; the port is short enough to
check by eye against the vendored copy.

Run: python3 -I research/wq4424_alexandrie/probe.py
Exit 1 means the fleet note's tags did not survive import.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def extract_metadata_from_markdown(markdown):
    metadata = {"title": ""}
    content_clean = markdown
    # JS: markdown.match(/^---\n([\s\S]*?)\n---/) -- no multiline flag, so ^ is start of string
    m = re.match(r"---\n([\s\S]*?)\n---", markdown)
    if m and m.group(1):
        for line in m.group(1).split("\n"):
            key, *rest = line.split(":")
            value = re.sub(r'^"(.*)"$', r"\1", ":".join(rest).strip())
            if key in ("title", "description", "tags"):
                metadata[key] = value
        content_clean = markdown[len(m.group(0)):].lstrip()
    return {**metadata, "content_clean": content_clean}


def parse_tags(tags):
    return [t.strip() for t in tags.split(",") if t.strip()]


def main():
    with open(os.path.join(HERE, "fixture_wq4424.md")) as f:
        fleet_note = f.read()
    cases = {
        "fleet_note": fleet_note,
        "block_list_tags": "---\ntags:\n  - eval\n  - obsidian\n---\n\nbody",
        "crlf": "---\r\ntags: a, b\r\n---\r\n\r\nbody",
    }
    failed = False
    for name, md in cases.items():
        out = extract_metadata_from_markdown(md)
        tags = parse_tags(out.get("tags", ""))
        print(json.dumps({
            "case": name,
            "tags": tags,
            "dropped_keys": [k for k in ("ticket", "kind", "date", "ref")
                             if k + ":" in md and k not in out],
            "frontmatter_left_in_body": out["content_clean"].startswith("---"),
            "wikilink_left_as_text": "[[" in out["content_clean"],
        }))
        if name == "fleet_note" and tags != ["eval", "obsidian"]:
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

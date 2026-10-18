#!/usr/bin/env python3
"""wq#4194 -- live, keyless probe for deepseek_search_fetch_client.py.

No DEEPSEEK_API_KEY is reachable from this worktree (it lives in
/home/lumen/agent/.env, outside this session's --add-dir grants), so this
cannot exercise the 200 path. It instead asserts the two endpoints are live
and returning the documented 401 shape for a missing key -- the same
boundary the "no key configured" guard clause exists for -- so the request
plumbing (URL, method, headers, JSON body) is proven correct against the
real API, not mocked.

Run: python3 -I research/tools/test_deepseek_search_fetch_client.py
"""
import sys

from deepseek_search_fetch_client import deepseek_fetch, deepseek_search


def check(label, result, err):
    assert result is None, f"{label}: expected no results without a key, got {result!r}"
    assert err, f"{label}: expected an error message"
    print(f"ok: {label}: {err}")


def main():
    check("no key configured", *deepseek_search("DeepSeek V4 release", api_key=""))
    check("no key configured", *deepseek_fetch("https://example.com", api_key=""))

    # Invalid-but-present key -> the live API itself must reject it (401),
    # proving the request reaches api.deepseek.com with the right shape.
    bad_key = "x" * 40
    results, err = deepseek_search("DeepSeek V4 release", api_key=bad_key)
    assert results is None, f"expected rejection, got {results!r}"
    assert "401" in err, f"expected a 401 from the live API, got: {err}"
    print(f"ok: live search endpoint rejects bad key: {err}")

    page, err = deepseek_fetch("https://example.com", api_key=bad_key)
    assert page is None, f"expected rejection, got {page!r}"
    assert "401" in err, f"expected a 401 from the live API, got: {err}"
    print(f"ok: live fetch endpoint rejects bad key: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

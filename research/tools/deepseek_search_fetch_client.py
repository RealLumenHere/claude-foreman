#!/usr/bin/env python3
"""wq#4194 -- DeepSeek Search/Fetch API client, reference implementation.

Companion to ../DEEPSEEK_SEARCH_FETCH_EVAL.md. Mirrors the
(results_or_None, error_or_None) return convention fleet-search's own
searxng_search()/brave_api_search() use (/home/lumen/bin/fleet-search:273-321,
a separate repo this branch cannot commit into -- see the eval doc's
"Follow-up" section) so a future wiring ticket can drop deepseek_search()
into cmd_web's fallback chain and deepseek_fetch() into _fetch_ladder()
with no shape changes.

Both endpoints share one bearer token (DEEPSEEK_API_KEY, already provisioned
fleet-wide for model calls per VENDOR_KEY_ROTATION.md) and are billed
per successful request, not by tokens: $0.75 / 1,000 for Search, $0.75 / 1,000
for Fetch (api-docs.deepseek.com/guides/search, "Billing" section, captured
2026-10-18).
"""
import json
import os
import urllib.error
import urllib.request

SEARCH_URL = "https://api.deepseek.com/v1/tools/search"
FETCH_URL = "https://api.deepseek.com/v1/tools/fetch"
TIMEOUT = 20


def _post(url, body, api_key):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, None
    except urllib.error.URLError as e:
        return 0, {"error": {"message": str(e)}}


def deepseek_search(query, n=10, need_content=False, api_key=None):
    """Returns (results_list_or_None, error_or_None).

    Each result dict carries title/url/snippet/summary/rerank_score/authority
    as documented -- rerank_score >= 1.5 is "high-quality" per the API docs,
    so callers can filter without a second ranking pass.
    """
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None, "no DEEPSEEK_API_KEY configured"
    status, data = _post(
        SEARCH_URL,
        {"query": query, "count": max(1, min(n, 50)), "need_content": need_content},
        api_key,
    )
    if status == 0:
        return None, f"deepseek search unreachable: {data['error']['message']}"
    if status == 401:
        return None, "deepseek search 401 -- key invalid/revoked"
    if status == 402:
        return None, "deepseek search 402 -- account balance exhausted"
    if status == 429:
        return None, "deepseek search 429 -- QPS limit exceeded (5 qps/account)"
    if status != 200 or data is None:
        msg = (data or {}).get("error", {}).get("message", "") if data else ""
        return None, f"deepseek search http {status}: {msg}"
    out = []
    for r in data.get("results", [])[:n]:
        out.append({
            "title": r.get("title", ""), "url": r.get("url", ""),
            "snippet": r.get("summary") or r.get("snippet", ""),
            "rerank_score": r.get("rerank_score", 0),
            "authority": r.get("authority", -1),
            "engine": "deepseek-search",
        })
    return out, None


def deepseek_fetch(url, api_key=None):
    """Returns (page_dict_or_None, error_or_None). page = {"title", "content"}
    with content as Markdown, matching fleet-search's _fetch_and_extract()
    output shape (/home/lumen/bin/fleet-search:733-783, separate repo)."""
    api_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None, "no DEEPSEEK_API_KEY configured"
    status, data = _post(FETCH_URL, {"url": url}, api_key)
    if status == 0:
        return None, f"deepseek fetch unreachable: {data['error']['message']}"
    if status == 401:
        return None, "deepseek fetch 401 -- key invalid/revoked"
    if status == 402:
        return None, "deepseek fetch 402 -- account balance exhausted"
    if status == 403:
        return None, "deepseek fetch 403 -- rejected by target site security policy"
    if status == 404:
        return None, "deepseek fetch 404 -- no usable page content"
    if status == 429:
        return None, "deepseek fetch 429 -- QPS limit exceeded (5 qps/account, also per-domain)"
    if status != 200 or data is None:
        msg = (data or {}).get("error", {}).get("message", "") if data else ""
        return None, f"deepseek fetch http {status}: {msg}"
    return {"title": data.get("title", ""), "content": data.get("content", "")}, None

"""
HTTP-level caching helpers (ETag + Cache-Control) for read-heavy DRF endpoints.

We already use Redis read-through caching to skip expensive DB work on
repeated requests for the same key. This module adds the *next* layer of
savings: when a polling client (React Query, mobile, CDN) sends back the
same ``If-None-Match`` we previously gave them, we short-circuit with a
``304 Not Modified`` and never serialize or transmit the JSON body again.

For a dashboard polled every 60s by hundreds of concurrent owners on
mostly-unchanged data, this turns the typical request from a 5–50 KB JSON
payload into an empty 304 — same DB load (zero, thanks to Redis), but a
fraction of the CPU and bandwidth on our servers and on the user's
network. Compounded over a day this is the single largest cheap win in
the request pipeline.

Usage:

    from core.http_caching import json_response_with_cache

    return json_response_with_cache(
        request,
        payload,
        max_age=55,            # browser/CDN treats data as fresh for 55s
        private=True,          # never cache cross-user
    )

It transparently returns a ``rest_framework.response.Response`` (with the
caching headers already attached) on first hit, or an HTTP 304 with the
same ``ETag`` when the client already has the same payload.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from rest_framework.response import Response


def _stable_etag(payload: Any) -> str:
    """
    Compute a deterministic, content-addressed ETag for a JSON-serialisable
    payload. ``sort_keys`` + ``default=str`` keeps the hash stable across
    Python dict insertion order and across UUID/datetime values that DRF
    will serialise the same way on the wire.
    """
    body = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()
    # Weak ETag ("W/") because our renderer may re-order keys and add
    # whitespace; we only guarantee semantic equivalence, not byte equality.
    return f'W/"{digest}"'


def json_response_with_cache(
    request,
    payload: Any,
    *,
    max_age: int = 30,
    private: bool = True,
    stale_while_revalidate: int | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    """
    Return either a ``304 Not Modified`` (if the client's ``If-None-Match``
    matches the freshly-computed ETag) or a normal ``Response`` carrying
    the payload plus ``ETag`` + ``Cache-Control`` headers.

    ``private`` keeps shared/edge caches from storing per-tenant data.
    ``stale_while_revalidate`` lets the browser keep showing the cached
    body for up to N more seconds while a background revalidation fires.
    """
    etag = _stable_etag(payload)
    inm = request.META.get("HTTP_IF_NONE_MATCH", "")
    if inm and etag in [v.strip() for v in inm.split(",")]:
        # 304: empty body, but we MUST echo back the same ETag and
        # Cache-Control so the client refreshes its freshness window.
        resp = Response(status=304)
    else:
        resp = Response(payload)

    visibility = "private" if private else "public"
    cc_parts = [visibility, f"max-age={max_age}"]
    if stale_while_revalidate is not None:
        cc_parts.append(f"stale-while-revalidate={stale_while_revalidate}")
    resp["Cache-Control"] = ", ".join(cc_parts)
    resp["ETag"] = etag
    # Critical on shared devices: key the browser HTTP cache by the auth
    # token so logging out/in as another user can never serve stale data
    # from the previous session. We also vary by Accept-Language because
    # several endpoints localise their text payload.
    existing_vary = resp.get("Vary", "")
    vary_tokens = {tok.strip() for tok in existing_vary.split(",") if tok.strip()}
    vary_tokens.update({"Authorization", "Accept-Language"})
    resp["Vary"] = ", ".join(sorted(vary_tokens))
    # Some intermediaries (and the Django test client) will collapse
    # bodies on 304s, but they MUST NOT collapse the headers we just set.
    # DRF's Response only attaches them when render() runs, which happens
    # after this function returns; that's fine for both 200 and 304.
    if extra_headers:
        for k, v in extra_headers.items():
            resp[k] = v
    return resp

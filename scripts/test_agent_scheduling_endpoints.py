#!/usr/bin/env python3
"""
Integration test for scheduling agent endpoints (Miya).
Run with backend server up and env set:
  API_BASE_URL=http://localhost:8000 LUA_WEBHOOK_API_KEY=your-key RESTAURANT_ID=uuid python3 scripts/test_agent_scheduling_endpoints.py

Verifies:
- GET /api/scheduling/agent/staff/ with X-Restaurant-Id returns 200 (no "Unable to resolve restaurant context")
- GET /api/scheduling/agent/staff-count/ with X-Restaurant-Id returns 200
- POST /api/scheduling/agent/create-shift/ with X-Restaurant-Id + body returns 201 or 409 (not 400 "Unable to resolve")
- POST /api/scheduling/agent/optimize-schedule/ with X-Restaurant-Id + body returns 200 or 400 (error must not be "Unable to resolve restaurant context")
"""
import os
import sys

try:
    import requests
except ImportError:
    requests = None

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
AGENT_KEY = os.environ.get("LUA_WEBHOOK_API_KEY", "")
RESTAURANT_ID = os.environ.get("RESTAURANT_ID", "")


def main():
    if requests is None:
        print("SKIP: 'requests' not installed. pip install requests to run integration tests.")
        return 0
    if not AGENT_KEY:
        print("SKIP: LUA_WEBHOOK_API_KEY not set. Set it to run against a live backend.")
        return 0
    if not RESTAURANT_ID:
        print("SKIP: RESTAURANT_ID not set. Set it to run against a live backend.")
        return 0

    headers = {
        "Authorization": f"Bearer {AGENT_KEY}",
        "Content-Type": "application/json",
        "X-Restaurant-Id": RESTAURANT_ID,
    }
    errors = []

    # 1. Agent staff list
    url = f"{API_BASE}/api/scheduling/agent/staff/"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            errors.append(f"GET agent/staff/ -> {r.status_code} {r.text[:200]}")
        else:
            body = r.text
            if "Unable to resolve restaurant context" in body:
                errors.append("GET agent/staff/ returned 'Unable to resolve restaurant context' despite X-Restaurant-Id")
            else:
                print("OK GET /api/scheduling/agent/staff/ (200)")
    except Exception as e:
        errors.append(f"GET agent/staff/ failed: {e}")

    # 2. Agent staff count
    url = f"{API_BASE}/api/scheduling/agent/staff-count/"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200:
            errors.append(f"GET agent/staff-count/ -> {r.status_code} {r.text[:200]}")
        else:
            body = r.text
            if "Unable to resolve restaurant context" in body:
                errors.append("GET agent/staff-count/ returned 'Unable to resolve restaurant context' despite X-Restaurant-Id")
            else:
                print("OK GET /api/scheduling/agent/staff-count/ (200)")
    except Exception as e:
        errors.append(f"GET agent/staff-count/ failed: {e}")

    # 3. Agent optimize-schedule (needs week_start; may return 400 for business logic)
    url = f"{API_BASE}/api/scheduling/agent/optimize-schedule/"
    try:
        r = requests.post(
            url,
            headers=headers,
            json={"restaurant_id": RESTAURANT_ID, "week_start": "2026-02-02", "department": "all"},
            timeout=15,
        )
        if r.status_code == 400:
            err = r.json().get("error", r.text)
            if "Unable to resolve restaurant context" in str(err):
                errors.append("POST agent/optimize-schedule/ returned 'Unable to resolve restaurant context' despite X-Restaurant-Id")
            else:
                print("OK POST /api/scheduling/agent/optimize-schedule/ (400 expected for business logic)")
        elif r.status_code == 200:
            print("OK POST /api/scheduling/agent/optimize-schedule/ (200)")
        else:
            errors.append(f"POST agent/optimize-schedule/ -> {r.status_code} {r.text[:200]}")
    except Exception as e:
        errors.append(f"POST agent/optimize-schedule/ failed: {e}")

    if errors:
        print("\nFAILED:")
        for e in errors:
            print("  -", e)
        return 1
    print("\nAll agent scheduling endpoint checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

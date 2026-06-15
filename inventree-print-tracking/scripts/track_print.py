#!/usr/bin/env python3
"""track_print.py — InvenTree build workflow for a 3D-printed part.

Usage:
  track_print.py <part_id> [--qty N] [--date YYYY-MM-DD] [--location ID] [--title TEXT]

Reads the InvenTree token and URL from the INV_TOKEN and INV_URL env vars.
Run from any directory. Requires Python 3.9+ (stdlib only — no pip deps).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date as date_t


def load_config():
    """Read InvenTree token and URL from env. Exit clearly if missing."""
    token = os.environ.get("INV_TOKEN")
    url = os.environ.get("INV_URL")
    if not token:
        sys.exit("INV_TOKEN is not set. Export it before running this script.")
    if not url:
        sys.exit("INV_URL is not set. Export it before running this script.")
    return token, url.rstrip("/")


TOKEN, URL = load_config()
REF = URL


def api(method, path, body=None):
    headers = {
        "Authorization": f"Token {TOKEN}",
        "Referer": REF,
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"HTTP {e.code}: {body}", file=sys.stderr)
        raise


def step(n, msg):
    print(f"\n=== {n}. {msg} ===")


def ok(msg):
    print(f"  ✓ {msg}")


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("part_id", type=int, help="InvenTree Part id to build")
    p.add_argument("--qty", type=int, default=1, help="Quantity to build (default 1)")
    p.add_argument("--date", default=date_t.today().isoformat(), help="Print date YYYY-MM-DD (default today)")
    p.add_argument("--location", type=int, default=1, help="Stock location id (default 1 = Warehouse)")
    p.add_argument("--title", default=None, help="Build title")
    args = p.parse_args()
    title = args.title or f"Print part {args.part_id} on {args.date}"

    # 1. Create Build
    step(1, f"Create Build Order (part {args.part_id}, qty {args.qty})")
    b = api("POST", "/api/build/", {
        "part": args.part_id,
        "quantity": args.qty,
        "title": title,
        "target_date": args.date,
    })
    build_id = b["pk"]
    ok(f"Build {build_id} created (ref={b.get('reference')})")

    # 2. Issue
    step(2, f"Issue build {build_id}")
    api("POST", f"/api/build/{build_id}/issue/", {})
    ok("Build in Production (status=20)")

    # 3. Read build lines
    step(3, "Read build_lines")
    bls = api("GET", f"/api/build/line/?build={build_id}")
    for bl in bls:
        print(f"  build_line pk={bl['pk']}  part={bl['part']}  qty={bl['quantity']}  ref={bl.get('reference','')}")

    # 4 + 5. Find stock + allocate
    step("4-5", "Find stock and allocate")
    items = []
    for bl in bls:
        stocks = api("GET", f"/api/stock/?part={bl['part']}&location={args.location}")
        if isinstance(stocks, dict):
            stocks = stocks.get("results", [])
        chosen = next(
            (s["pk"] for s in stocks if float(s.get("quantity", 0)) >= bl["quantity"]),
            None,
        )
        if not chosen:
            sys.exit(
                f"No stock for part {bl['part']} qty {bl['quantity']} in location {args.location}"
            )
        items.append({
            "build_line": bl["pk"],
            "stock_item": chosen,
            "quantity": bl["quantity"],
        })
        ok(f"build_line {bl['pk']} (part {bl['part']}) ← stock {chosen}")
    api("POST", f"/api/build/{build_id}/allocate/", {"items": items})
    ok("Stock allocated")

    # 6. Consume
    step(6, "Consume stock")
    api("POST", f"/api/build/{build_id}/consume/", {
        "lines": [{"build_line": bl["pk"], "quantity": bl["quantity"]} for bl in bls],
    })
    ok("Stock consumed (build_lines.consumed updated)")

    # 7. Create incomplete output
    step(7, "Create incomplete output (status=50 = in production)")
    out = api("POST", "/api/stock/", {
        "part": args.part_id,
        "build": build_id,
        "location": args.location,
        "quantity": args.qty,
        "batch": args.date,
        "status": 50,
        "notes": f"In production — print {args.date}",
    })
    out_id = out[0]["pk"] if isinstance(out, list) else out["pk"]
    ok(f"Output StockItem {out_id}")

    # 8. Mark output as in-production (required before /complete/)
    step(8, "Mark output as in-production (is_building=true)")
    api("PATCH", f"/api/stock/{out_id}/", {"is_building": True})
    ok(f"StockItem {out_id} flagged is_building=true")

    # 9. Complete build outputs (bumps build.completed, sets StockItem to OK)
    step(9, "Complete build outputs (bumps build.completed, sets StockItem status to OK)")
    api("POST", f"/api/build/{build_id}/complete/", {
        "outputs": [{"output": out_id, "quantity": args.qty}],
        "location": args.location,
        "status_custom_key": 10,
        "accept_incomplete_allocation": True,
    })
    ok(f"Build {build_id} outputs completed (build.completed += {args.qty})")

    # 10. Finish build
    step(10, f"Finish build {build_id}")
    api("POST", f"/api/build/{build_id}/finish/", {
        "accept_overallocated": "reject",
        "accept_unallocated": True,
        "accept_incomplete": True,
    })
    ok("Build DONE (status=40)")

    # 11. Backdate
    step(11, f"Backdate dates to {args.date}")
    api("PATCH", f"/api/build/{build_id}/", {
        "start_date": args.date,
        "target_date": args.date,
        "completion_date": args.date,
    })
    ok("start/target/completion_date set")

    print(f"\n✓ Build {build_id} complete. Output: StockItem {out_id}.")


if __name__ == "__main__":
    main()

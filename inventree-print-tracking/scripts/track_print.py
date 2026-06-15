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


def fatal(msg):
    """Print a fatal error and exit."""
    print(f"\n  ✗ FATAL: {msg}", file=sys.stderr)
    sys.exit(1)


def get_build_lines(build_id):
    """Read build lines, return as a list (not paginated dict)."""
    bls = api("GET", f"/api/build/line/?build={build_id}")
    return bls if isinstance(bls, list) else bls.get("results", [])


def unconsumed_lines(build_lines):
    """Filter to lines where consumed < quantity (idempotency guard)."""
    return [bl for bl in build_lines if float(bl.get("consumed", 0)) < float(bl["quantity"])]


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
    bls = get_build_lines(build_id)
    for bl in bls:
        print(f"  build_line pk={bl['pk']}  part={bl['part']}  qty={bl['quantity']}  consumed={bl.get('consumed', 0)}")

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
            fatal(f"No stock for part {bl['part']} qty {bl['quantity']} in location {args.location}")
        items.append({
            "build_line": bl["pk"],
            "stock_item": chosen,
            "quantity": bl["quantity"],
        })
        ok(f"build_line {bl['pk']} (part {bl['part']}) ← stock {chosen}")
    api("POST", f"/api/build/{build_id}/allocate/", {"items": items})
    ok("Stock allocated")

    # 6. Re-read build_lines and filter to unconsumed (idempotency guard).
    #    InvenTree 1.2.7's /consume/ is NOT idempotent — re-submitting creates
    #    duplicate child stock items and doubles build_line.consumed.
    step(6, "Re-read build_lines, filter to unconsumed (idempotency guard)")
    bls = get_build_lines(build_id)
    to_consume = unconsumed_lines(bls)
    if not to_consume:
        ok("All build_lines already consumed — skipping /consume/ (idempotency guard)")
    else:
        for bl in to_consume:
            print(f"  build_line {bl['pk']}  part={bl['part']}  qty={bl['quantity']}  consumed={bl.get('consumed', 0)} (will consume)")

    # 7. Consume
    if to_consume:
        step(7, "Consume stock (only for unconsumed lines)")
        api("POST", f"/api/build/{build_id}/consume/", {
            "lines": [{"build_line": bl["pk"], "quantity": bl["quantity"]} for bl in to_consume],
        })
        ok("Stock consumed")

    # 8. Create incomplete output
    step(8, "Create incomplete output (status=50 = in production)")
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

    # 9. Mark output as in-production
    step(9, "Mark output as in-production (is_building=true)")
    api("PATCH", f"/api/stock/{out_id}/", {"is_building": True})
    ok(f"StockItem {out_id} flagged is_building=true")

    # 10. Complete build outputs
    step(10, "Complete build outputs (bumps build.completed, sets StockItem status to OK)")
    api("POST", f"/api/build/{build_id}/complete/", {
        "outputs": [{"output": out_id, "quantity": args.qty}],
        "location": args.location,
        "status_custom_key": 10,
        "accept_incomplete_allocation": True,
    })
    ok(f"Build {build_id} outputs completed (build.completed += {args.qty})")

    # 11. Finish build
    step(11, f"Finish build {build_id}")
    api("POST", f"/api/build/{build_id}/finish/", {
        "accept_overallocated": "reject",
        "accept_unallocated": True,
        "accept_incomplete": True,
    })
    ok("Build DONE (status=40)")

    # 12. Backdate
    step(12, f"Backdate dates to {args.date}")
    api("PATCH", f"/api/build/{build_id}/", {
        "start_date": args.date,
        "target_date": args.date,
        "completion_date": args.date,
    })
    ok("start/target/completion_date set")

    # 13. VERIFY (mandatory) — catch the doubling bug or any other silent inconsistency
    step(13, "VERIFY (mandatory) — catch silent inconsistencies before declaring done")
    final = api("GET", f"/api/build/{build_id}/")
    bls_final = get_build_lines(build_id)
    out_final = api("GET", f"/api/stock/{out_id}/")

    ok_or_fail = []
    if int(final["status"]) != 40:
        ok_or_fail.append(f"build.status={final['status']} (expected 40)")
    if float(final["completed"]) != float(final["quantity"]):
        ok_or_fail.append(f"build.completed={final['completed']} (expected {final['quantity']})")

    for bl in bls_final:
        if float(bl["consumed"]) != float(bl["quantity"]):
            ok_or_fail.append(
                f"build_line {bl['pk']} consumed={bl['consumed']} (expected {bl['quantity']})"
            )

    if int(out_final["status"]) != 10:
        ok_or_fail.append(f"output StockItem {out_id} status={out_final['status']} (expected 10)")
    if out_final.get("is_building"):
        ok_or_fail.append(f"output StockItem {out_id} is_building={out_final['is_building']} (expected false)")

    if ok_or_fail:
        print()
        print("  ✗ VERIFICATION FAILED — DO NOT DECLARE THIS BUILD COMPLETE:")
        for line in ok_or_fail:
            print(f"    - {line}")
        fatal("InvenTree state is inconsistent. Investigate before proceeding.")

    print()
    print(f"  ✓✓✓ All checks passed: build.completed={final['completed']}/{final['quantity']}, all build_lines consumed correctly, output OK")
    print(f"\n✓ Build {build_id} complete. Output: StockItem {out_id}.")


if __name__ == "__main__":
    main()

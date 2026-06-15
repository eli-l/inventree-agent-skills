---
name: "inventree-print-tracking"
description: "Track 3D-printed parts in InvenTree: build, allocate, consume, finish. Use when the user says 'I printed X' or 'track a print'."
---

# InvenTree Print Tracking

**Use when:** the user reports a finished 3D print, asks to track a print, register a build, or consume materials for a finished print. Trigger phrases: "I printed X", "track a print", "register a build", "consume materials for X".

**Don't use** for parts that haven't been printed yet, or for non-3D-printed items (purchased parts, virtual parts).

## The workflow (in order)

1. **Create Build Order** — `POST /api/build/`
2. **Issue the build** — `POST /api/build/{id}/issue/` (status 10 → 20)
3. **Read build_lines** — `GET /api/build/line/?build={id}` (separate model from BOM)
4. **Find matching stock** — pick StockItems where `part == build_line.part` and `quantity >= build_line.quantity`
5. **Allocate stock** — `POST /api/build/{id}/allocate/` with `items[]`
6. **Read build_lines, filter unconsumed** — re-read `/api/build/line/?build={id}` and skip lines where `consumed >= quantity`. This guards against double-submit on retry (InvenTree 1.2.7 does NOT dedupe `/consume/` — re-running it creates duplicate child stock_items and doubles `build_line.consumed`).
7. **Consume stock** — `POST /api/build/{id}/consume/` with `lines[]` (only for the unconsumed lines from step 6)
8. **Create incomplete output** — `POST /api/stock/` with `status=50` (Attention), `build=<id>`, `location=<id>`
9. **Mark output as in-production** — `PATCH /api/stock/{id}/` with `is_building=true`. **Required** before `/complete/` will accept the output.
10. **Complete build outputs** — `POST /api/build/{id}/complete/` with `outputs[]: [{"output": <stock_item_pk>, "quantity": <n>}]`, `location`, `status_custom_key=10`. **Required** to bump `build.completed`. Sets output StockItem to OK and clears `is_building`.
11. **Finish build** — `POST /api/build/{id}/finish/` (status 20 → 40)
12. **Backdate dates** — `PATCH /api/build/{id}/` with `start_date`, `target_date`, `completion_date`
13. **VERIFY (mandatory)** — re-read `/api/build/{id}/` and `/api/build/line/?build={id}`. Assert:
    - `build.status == 40`
    - `build.completed == build.quantity`
    - For every build_line: `consumed == quantity` (NOT 2x, NOT 0x)
    - The output StockItem exists, has `is_building=false`, `status=10`
    
    If any check fails, **STOP and report the discrepancy to the user** — do not declare the build complete.

## Why steps 6 and 13 are mandatory

InvenTree 1.2.7's `/consume/` endpoint is **NOT idempotent**. Re-submitting the same payload (e.g. after a crashed exec is retried) creates a duplicate child StockItem per resubmission and doubles `build_line.consumed`. Confirmed on 2026-06-15 against ops.mistit.com: builds 24 and 25 ended up with `consumed = 2 × quantity` because the previous exec had crashed after POSTing `/consume/` but before reading the response, then a retry exec re-POSTed the same payload. The server processed both. The build_lines were overstated by 552g of Gray and 40g of Orange even though the actual stock movement was 1x.

Step 6 (filter unconsumed) prevents the double-submit at submission time. Step 13 (verify) catches it after the fact in case step 6 was bypassed or some other mechanism caused the doubling.

The doubling cannot be fixed via API for a finished build — only the build's `completed` and dates are writable; `build_line.consumed` is writable via `/api/build/line/{id}/` PATCH but requires manual intervention.

## Why step 9 is mandatory

The build's `completed` count is **not** updated by `/finish/`. It's a separate field that only `/complete/` touches. But `/complete/` will reject the call with `"This build output has already been completed"` if the output StockItem is not in the right state. The actual contract is:

1. StockItem must have `is_building=true` (set via PATCH in step 9, NOT auto-set on creation)
2. StockItem must be linked to the build (via `build=<id>` at creation in step 8)
3. Build must be in status 20 or 30

If any of these are wrong, `/complete/` either errors out or silently no-ops. Skipping these steps leaves the build in `status=40` (Complete) but `completed=0` — a silent data inconsistency.

The `/complete/` endpoint also sets the output StockItem to `status_custom_key` (default 10=OK) and clears `is_building`, so it replaces the old "PATCH StockItem to status=10" step.

## Inputs to gather (ask the user if not provided)

- **Part** — name, IPN, or Part id
- **Quantity** — default 1
- **Print date** — defaults to today
- **Output location** — defaults to Warehouse (id=1)
- **Filament** — usually matches the Part's BOM; ask if overriding

If the Part or its BOM doesn't exist, create them first.

## Auth

Set these in your environment (shell rc, `.env`, secrets manager, etc.):

```bash
export INV_TOKEN="***"   # InvenTree REST API token
export INV_URL="https://inventree.example.com"   # base URL of your InvenTree instance
export INV_REF="$INV_URL"                        # used as the Referer header
```

The token is read at runtime — **never hardcode it** in the skill files or commit it.

## API gotchas (the most common blockers)

| Endpoint | Body field | Notes |
|---|---|---|
| `POST /api/build/{id}/allocate/` | `items[]` | Each: `build_line`, `stock_item`, `quantity`. Parts MUST match. |
| `POST /api/build/{id}/consume/` | `lines[]` | **NOT idempotent in 1.2.7.** Each: `build_line`, `quantity`. Re-submitting creates duplicate child stock items and doubles `build_line.consumed`. **Always filter to unconsumed lines first (step 6) and verify after (step 13).** |
| `PATCH /api/stock/{id}/` (after output creation) | `is_building=true` | **Required** before `/complete/`. `is_building` is writable, NOT auto-set on creation, but `/complete/` requires it. |
| `POST /api/build/{id}/complete/` | `outputs[]`, `location`, `status_custom_key` | **Required** to bump `build.completed`. Each output: `{"output": <stock_item_pk>, "quantity": <n>}`. Top-level `location` is required. Requires build in status 20 or 30. Output StockItem must have `is_building=true`. Sets the output StockItem to `status_custom_key` (default 10=OK) and clears `is_building`. |
| `POST /api/build/{id}/finish/` | flags | `accept_overallocated`, `accept_unallocated`, `accept_incomplete`. Transitions 20→40. **Does NOT update `completed`** — that's `/complete/`'s job. |
| `POST /api/stock/` | full fields | Returns a **LIST** (not single object). |
| `POST /api/stock/{id}/adjust/` | — | **DOES NOT EXIST**. Use the MCP `adjust_stock` tool. |
| `POST /api/build/{id}/issue/` | empty `{}` | Transitions 10 → 20. |
| `GET /api/build/line/?build={id}` | n/a | Different from `/api/bom/`. |
| `POST /api/build/` | part, quantity, target_date | `creation_date`, `start_date`, `status` are server-controlled. |
| `PATCH /api/build/{id}/` | start/target/completion_date | `creation_date` and `completed` are read-only. |
| `PATCH /api/build/line/{id}/` | `consumed` | Writable — useful for repairing the doubling bug after the fact, since `/api/build/{id}/` doesn't allow editing `completed` and the build_line PATCH is the only path back to consistency. |

## StockItem status enum (for output state)

- `10` = OK / Complete
- `50` = Attention (use for "in production / incomplete")
- `55` = Damaged
- `60` = Destroyed
- `65` = Lost
- `70` = Rejected

`is_building` is a separate boolean on StockItem. It is **NOT** auto-set on creation — you must PATCH it explicitly. `/complete/` requires it to be `true`; it gets cleared on successful completion.

## Build status enum (the state machine)

- `10` = Pending
- `20` = Production (via `/issue/`)
- `30` = Complete (intermediate)
- `40` = Complete / DONE (via `/finish/`)

**Can't PATCH `status` directly** — only action endpoints change it.

## build_line ↔ BOM_item relationship

`/api/build/line/?build={id}` returns a separate model from BOM. Build_lines are created automatically when you `/issue/` a build. Each build_line has a `bom_item` field referencing the source BOM item, plus `part` (denormalized), `quantity`, `allocated`, `consumed`.

Common pitfall: assuming `build_line.pk == bom_item.pk`. They're different IDs.

## Backdating

- `creation_date` is read-only on PATCH (stays at actual creation time)
- `start_date`, `target_date`, `completion_date` ARE writable on PATCH
- `completed` is read-only — only updated by `/complete/`

## Example: Plate Cleaner (2026-06-12)

The user printed Part 41 (`PLATE-CLEANER`) on 2026-04-24. The executed workflow:

```
Build 18 (BO-0016) — Part 41, qty=1
├── /issue/              → status 20 (Production)
├── /allocate/           → build_line 29 (36g ← stock 26), build_line 30 (7g ← stock 32)
├── /consume/            → build_line 29 consumed=36, build_line 30 consumed=7
├── Create StockItem 66  → part 41, build 18, status 50 (Attention)
├── PATCH StockItem 66   → is_building=true (required before /complete/)
├── /complete/           → outputs: [{output: 66, quantity: 1}], status_custom_key: 10
│                          → StockItem 66 status 50→10, is_building→false, build.completed: 0→1
├── /finish/             → status 40 (DONE)
└── PATCH dates          → start/target/completion = 2026-04-24
```

Net effect: StockItem 26 (PLA Gray) 1000g → 964g; StockItem 32 (PLA Pumpkin Orange) 1000g → 993g; StockItem 66 (Plate Cleaner) qty=1 OK in Warehouse, batch=2026-04-24, build 18 completed=1/1.

## When to ask the user first

- The Part doesn't exist (need to create it + its BOM first)
- The BOM doesn't cover all the materials needed
- Available stock is insufficient (no StockItem with enough qty)
- The output location is unclear (default: Warehouse = id 1)
- Multiple Parts match the name

## See also

- `scripts/track_print.py` — one-shot Python script for the full workflow

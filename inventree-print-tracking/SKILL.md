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
6. **Create incomplete output** — `POST /api/stock/` with `status=50` (Attention)
7. **Consume stock** — `POST /api/build/{id}/consume/` with `lines[]`
8. **Complete output** — `PATCH /api/stock/{id}/` with `status=10` (OK)
9. **Finish build** — `POST /api/build/{id}/finish/` (status 20 → 40)
10. **Backdate dates** — `PATCH /api/build/{id}/` with `start_date`, `target_date`, `completion_date`

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
export INV_TOKEN="<your-inventree-api-token>"   # InvenTree REST API token
export INV_URL="https://inventree.example.com"   # base URL of your InvenTree instance
export INV_REF="$INV_URL"                        # used as the Referer header
```

The token is read at runtime — **never hardcode it** in the skill files or commit it.

## API gotchas (the most common blockers)

| Endpoint | Body field | Notes |
|---|---|---|
| `POST /api/build/{id}/allocate/` | `items[]` | Each: `build_line`, `stock_item`, `quantity`. Parts MUST match. |
| `POST /api/build/{id}/consume/` | `lines[]` | Each: `build_line`, `quantity`. **NOT `items`**. |
| `POST /api/build/{id}/finish/` | flags | `accept_overallocated`, `accept_unallocated`, `accept_incomplete`. |
| `POST /api/build/{id}/complete/` | `outputs[]` | Awkward — **avoid**; use `/finish/`. |
| `POST /api/stock/` | full fields | Returns a **LIST** (not single object). |
| `POST /api/stock/{id}/adjust/` | — | **DOES NOT EXIST**. Use the MCP `adjust_stock` tool. |
| `POST /api/build/{id}/issue/` | empty `{}` | Transitions 10 → 20. |
| `GET /api/build/line/?build={id}` | n/a | Different from `/api/bom/`. |
| `PATCH /api/stock/{id}/` | partial fields | `status` writable. `is_building` is NOT auto-set. |
| `POST /api/build/` | part, quantity, target_date | `creation_date`, `start_date`, `status` are server-controlled. |
| `PATCH /api/build/{id}/` | start/target/completion_date | `creation_date` is read-only. |

## StockItem status enum (for output state)

- `10` = OK / Complete
- `50` = Attention (use for "in production / incomplete")
- `55` = Damaged
- `60` = Destroyed
- `65` = Lost
- `70` = Rejected

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

## Example: Plate Cleaner (2026-06-12)

The user printed Part 41 (`PLATE-CLEANER`) on 2026-04-24. The executed workflow:

```
Build 18 (BO-0016) — Part 41, qty=1
├── /issue/              → status 20 (Production)
├── /allocate/           → build_line 29 (36g ← stock 26), build_line 30 (7g ← stock 32)
├── Create StockItem 66  → part 41, build 18, status 50 (Attention = in-production)
├── /consume/            → build_line 29 consumed=36, build_line 30 consumed=7
├── PATCH StockItem 66   → status 10 (OK = complete)
├── /finish/             → status 40 (DONE)
└── PATCH dates          → start/target/completion = 2026-04-24
```

Net effect: StockItem 26 (PLA Gray) 1000g → 964g; StockItem 32 (PLA Pumpkin Orange) 1000g → 993g; StockItem 66 (Plate Cleaner) qty=1 OK in Warehouse, batch=2026-04-24.

## When to ask the user first

- The Part doesn't exist (need to create it + its BOM first)
- The BOM doesn't cover all the materials needed
- Available stock is insufficient (no StockItem with enough qty)
- The output location is unclear (default: Warehouse = id 1)
- Multiple Parts match the name

## See also

- `scripts/track_print.py` — one-shot Python script for the full workflow

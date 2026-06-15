---
name: inventree-build-export
description: Export InvenTree Build Orders to XLSX for review, audit, and hand-off. Use when the user asks to "export build orders", "BO consumption report", "build audit", "show me what was built and what materials were used", or needs to see produced-vs-consumed material across all (or some) Build Orders. Reads the read-only InvenTree REST API and writes one row per (BO × build_line × consumed child StockItem) into ./export/.
---

# inventree-build-export

Exports InvenTree Build Orders to XLSX. The data model is:

> One row per **(Build Order × build_line × consumed child StockItem)**.

The left half of every row is **PRODUCED** (constant per BO). The right
half is **CONSUMED** (one row per child StockItem that the BO drained,
so a build that pulled filament from two spools gets two rows).
Source is either a PO (filament) or a BO (sub-assembly).

## When to use

Trigger phrases: "export build orders", "BO consumption report", "build
audit", "show me what was built and what was used", "what materials
went into this build", "XLSX export of all builds". Use any time the
user wants a tabular view of produced-vs-consumed material across one
or more Build Orders.

Don't use for: actively consuming / producing stock (use the
`inventree-print-tracking` skill), creating new BOs, or modifying data
— this skill is read-only.

## Quick start

```bash
# Required env (never hardcode these):
export INV_URL="https://inventree.example.com"
export INV_TOKEN="<your token>"

# Run it (writes to ./export/inventree_builds_<timestamp>.xlsx):
./scripts/export_builds.py
```

Path components on the outfile argument are dropped — output always
lands in `./export/`. This is hard-coded in `_resolve_outfile()`.

## Output schema (22 columns)

### Build Order (constant per BO)
| # | Column | Notes |
|---|--------|-------|
| 1 | BO Reference | e.g. `BO-0024` |
| 2 | BO Status | Pending / Production / Complete |
| 3 | BO Started | |
| 4 | BO Completed On | |

### Produced (constant per BO)
| # | Column | Notes |
|---|--------|-------|
| 5 | Produced Part IPN | IPN of the assembly this BO builds |
| 6 | Produced Part Name | Human-readable name |
| 7 | Produced Qty | Sum of output StockItem quantities |
| 8 | Production Cost | Recursive EUR cost of producing the BO's output (filament = qty × StockItem.purchase_price; sub-assemblies = sum of children's costs). See "Cost algorithm" below. |
| 9 | Currency | Currency for Production Cost (from the filament source; `mixed` if multiple currencies; empty if unknown) |
| 10 | Output StockItems | pk(s) of the StockItem(s) this BO created (semicolon-joined) |

### Build-line sub-part (constant per build_line)
| # | Column | Notes |
|---|--------|-------|
| 11 | BOM Sub-Part IPN | Generic — placeholder in the BOM (e.g. "PETG") |
| 12 | BOM Sub-Part Name | |
| 13 | BOM Qty / BO Unit | Quantity per 1 BO unit |

### Consumed (varies per row — one row per child StockItem)
| # | Column | Notes |
|---|--------|-------|
| 14 | Consumed Part IPN | IPN of the **specific** material actually used |
| 15 | Consumed Part Name | |
| 16 | Consumed Qty | Drained from the child StockItem (**not** `build_line.consumed`) |
| 17 | Consumed SI pk | The child StockItem |
| 18 | Consumed Cost | This child StockItem's contribution to Production Cost |

### Source (varies per row)
| # | Column | Notes |
|---|--------|-------|
| 19 | Source Type | `purchase_order` (filament) \| `build` (sub-assembly) \| `manual` \| blank |
| 20 | Source Ref | PO ref for filament, BO ref for sub-assembly |
| 21 | Source SI pk | The StockItem the child was drawn from |
| 22 | Source Batch | |

**References use IPN throughout.** Where IPN is missing the cell shows
`<name>@pk<n>` as a fallback so the row is still uniquely referenceable.

## Data-quality notes

- **Pending BOs** have no consumed children — the produced columns are
  filled but the consumed / source columns are blank. Useful as a
  "what's still to do" view.
- **Sub-assemblies**: a StockItem produced by BO-X and consumed by BO-Y
  has `build=X, consumed_by=Y`. The script counts it as output of X
  AND as input to Y (both views are valid).
- **1:N consumption**: a single build_line can drain multiple child
  StockItems. The script shows them all — one row per child. A
  build_line that pulled from 2 spools of the same IPN is **not** merged.
- **Consumed Qty vs build_line.consumed**: the export uses the child
  StockItem's actual drained qty, not `build_line.consumed`. Some
  historical build_lines have doubled `consumed` values from a server
  issue — that bug only affects the build_line counter, not the stock
  ledger, so the export is unaffected.

## Cost algorithm (recursive)

The Production Cost column is computed once per StockItem, then summed
per BO. The recursion is:

- If a StockItem has `purchase_price > 0` and no producing BO → it is
  filament (or other purchased stock) and its cost is
  `quantity * purchase_price`. InvenTree stores the per-unit price on
  the StockItem itself in the same unit as `quantity` (e.g. 0.01 EUR/g
  for PETG), so no spool-mass conversion is needed.
- If a StockItem has a producing BO (`build` set) and no
  `purchase_price` → it is a sub-assembly. Its cost is the sum of
  costs of the StockItems consumed by that BO.
- Otherwise (no price, no BO, no children) → cost is 0.

Memoised with cycle detection. Computed for every consumed StockItem
in the catalog, so per-BO Production Cost is a flat sum afterwards.

### Worked example (BO-0001 = MHY-FRAME)

```
BO-0001 (FRAME)            Production Cost = 16.00 EUR
├── MHY-FRAME-BF     3.80   <- 380g PETG Black @ 0.01 EUR/g
├── MHY-FRAME-BR     3.80   <- 380g PETG Black @ 0.01 EUR/g
├── MHY-FRAME-CS     0.80   <-  80g PETG Green @ 0.01 EUR/g
├── MHY-FRAME-DT     0.60   <-  60g PETG Green @ 0.01 EUR/g
├── MHY-FRAME-TF     4.00   <- 400g PETG Black @ 0.01 EUR/g   (380g + 20g from 2 spools)
├── MHY-FRAME-TR     2.20   <- 220g PETG Black @ 0.01 EUR/g
└── MHY-MESH         0.80   <-  80g PETG Green @ 0.01 EUR/g
                           sum = 16.00 EUR
```

The Consumed Cost column shows the per-child contribution. For
composite children, that contribution is itself a rollup: e.g. when
BO-0009 (CASE) consumes the FRAME sub-assembly, the consumed-cost for
that row equals 16.00 (the entire production cost of the FRAME).

## Pairing algorithm (build_line → consumed child)

The script pairs each build_line to its consumed child StockItems
in two passes:

1. **Exact part match**: `child.part == build_line.part`. Handles
   specific BOMs (e.g. "PETG White 1.75mm Filacraft").
2. **Largest-unused fallback** for build_lines with no exact match:
   assign remaining unused children to whichever build_line needs the
   most material (by `bom.quantity` desc). Handles generic BOMs
   (e.g. "PETG" with specific stock "PETG Black" and "PETG White").

The 1:N result is a mapping `{build_line.pk: [child, child, ...]}`.

## InvenTree API access

Read-only. Required endpoints:
- `/api/build/`, `/api/build/line/`, `/api/build/{pk}/`
- `/api/part/`, `/api/stock/`, `/api/stock/location/`

## Layout

```
inventree-build-export/
├── SKILL.md                 # this file
├── README.md
├── scripts/
│   └── export_builds.py     # the only executable
└── export/                  # output lands here (gitignored at the repo root)
```

The repo-root `.gitignore` matches `**/export/` and `**/export/*`, so
this skill's `export/` folder is never committed — and the same pattern
will work for any future skill that drops files into an `export/`
directory.

## Requirements

- Python 3.10+
- `openpyxl` (`pip install openpyxl`)

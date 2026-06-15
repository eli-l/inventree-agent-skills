# inventree-build-export

Export InvenTree Build Orders to XLSX for review, audit, and hand-off.

One row per `(Build Order × build_line × consumed child StockItem)`. The
left side of every row is the **PRODUCED** item (constant per BO), the
right side is the **CONSUMED** material (one row per child StockItem that
the BO drained). Source is either a PO (filament) or a BO (sub-assembly).

## Usage

```bash
export INV_URL=https://inventree.example.com
export INV_TOKEN=*** your token
./scripts/export_builds.py                # writes to ./export/<timestamp>.xlsx
./scripts/export_builds.py my-report.xlsx # writes to ./export/my-report.xlsx
```

Path components on the outfile argument are stripped — output always
lands in `./export/`. This keeps the directory safe to commit.

## Output

- `export/` (gitignored) — one or more `.xlsx` files, named after
  timestamp or your chosen filename. Re-run to refresh.

## Requirements

- Python 3.10+
- `openpyxl` (`pip install openpyxl`)

## InvenTree API

The script uses the read-only InvenTree REST API. Required access:
- `/api/build/`, `/api/build/line/`, `/api/build/{pk}/`
- `/api/part/`, `/api/stock/`, `/api/stock/location/`

## License

Private.

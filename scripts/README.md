# Scripts

| Script | What it does |
|---|---|
| `make_data.py` | Builds the synthetic lab database (`data/labops.duckdb`) and `data/ground_truth.json` (planted incidents and defects with exact counts). |
| `make_datasets.py` | Writes 15 sample workbooks to `sample_data/`. |
| `make_brand.py` | Regenerates logo and favicons from `analyst/static/brand/logo_canva_export.png` (needs Pillow). |

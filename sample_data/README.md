# Sample datasets

Synthetic, anonymised files for trying **Upload Excel** (15 files: lab, business and clinical shapes, a deliberately messy one, a 60,000-row CSV and an 80-column file). They are generated, not real data.

Regenerate with `python scripts/make_datasets.py` (deterministic). `tests/test_sample_data.py` checks that every file uploads cleanly. Upload limits: 15 MB, 10 sheets, 200,000 rows, 100 columns.

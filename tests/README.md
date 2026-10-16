# Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

No API key or network is needed: models are replaced by fake clients. One Docker sandbox test is skipped unless Docker is running. The suite builds its own synthetic database in a temp folder.

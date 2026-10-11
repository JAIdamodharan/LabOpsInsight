"""Turn an uploaded Excel (.xlsx) or CSV file into typed DuckDB tables, safely.

Uploads are untrusted input, so: only .xlsx/.csv (no macros, no legacy .xls), size / expansion / row / column / cell caps,
defusedxml is used by openpyxl when installed (XML bombs), formulas are read as their cached values and never evaluated,
hidden sheets are skipped, and nothing is ever executed. Values are kept as recorded: numbers stored as text stay text
(the data-review checks flag them) instead of being silently converted.
"""
import datetime as dt
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

MAX_BYTES = 15_000_000
MAX_UNCOMPRESSED = 120_000_000
MAX_ZIP_ENTRIES = 2000
MAX_SHEETS = 10
MAX_ROWS = 200_000
MAX_COLS = 100
MAX_CELLS = 3_000_000
MAX_NAME = 60

# words DuckDB will not accept as bare identifiers: we suffix them so the agent's unquoted SQL just works
RESERVED = set("""all analyse analyze and any array as asc asymmetric both case cast check collate column constraint create default
deferrable desc describe distinct do else end except false fetch for foreign from grant group having in initially intersect into
lateral leading limit not null offset on only or order pivot placing primary qualify references returning select show some summarize
symmetric table then to trailing true union unique unpivot using variadic when where window with""".split())


class IngestError(ValueError):
    """A problem with the uploaded file that the user can fix; the message is safe to show."""


@dataclass
class TableMeta:
    name: str
    sheet: str
    rows: int
    columns: list[dict]  # {name, original, type}


@dataclass
class IngestResult:
    tables: list[TableMeta] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def ident(raw, fallback: str, used: set[str]) -> str:
    s = re.sub(r"\W+", "_", str(raw).strip().lower()).strip("_") or fallback
    if s[0].isdigit():
        s = f"{fallback.split('_')[0]}_{s}"
    s = s[:MAX_NAME]
    if s in RESERVED:
        s += "_"
    base, i = s, 2
    while s in used:
        s = f"{base}_{i}"
        i += 1
    used.add(s)
    return s


def _check_zip(data: bytes) -> None:
    if data[:8] == bytes.fromhex("D0CF11E0A1B11AE1"):
        raise IngestError("This looks like a legacy .xls file or a password-protected workbook. Open it in Excel and save a copy as .xlsx without a password.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise IngestError("This is not a valid .xlsx file (it is not an Excel zip package). Re-save it from Excel as .xlsx.")
    infos = z.infolist()
    names = {i.filename for i in infos}
    if len(infos) > MAX_ZIP_ENTRIES:
        raise IngestError("This workbook has an unusual number of internal parts and was rejected.")
    if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise IngestError(f"The workbook expands to more than {MAX_UNCOMPRESSED // 10**6} MB of data, which is over the limit.")
    if "xl/workbook.xml" not in names:
        raise IngestError("This zip file is not an Excel workbook.")
    if any(n.startswith("xl/vbaProject") for n in names):
        raise IngestError("Macro-enabled workbooks are not accepted. Save a copy as a plain .xlsx (no macros) and upload that.")


def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _column(values: list, label: str, warnings: list[str]):
    """Infer one column's type from its cell values; returns a pandas Series."""
    vals = [None if _is_blank(v) else v for v in values]
    nn = [v for v in vals if v is not None]
    if not nn:
        return pd.Series([None] * len(vals), dtype=object)
    if all(isinstance(v, bool) for v in nn):
        return pd.Series(pd.array(vals, dtype="boolean"))
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in nn):
        if all(isinstance(v, int) and abs(v) < 2**62 for v in nn):
            return pd.Series(pd.array(vals, dtype="Int64"))
        return pd.Series([float(v) if v is not None else float("nan") for v in vals], dtype="float64")
    if all(isinstance(v, dt.datetime) and v.tzinfo is None for v in nn):
        if all(v.time() == dt.time(0) for v in nn):
            return pd.Series([v.date() if v is not None else None for v in vals], dtype=object)
        return pd.Series(vals, dtype=object)
    if all(isinstance(v, dt.date) and not isinstance(v, dt.datetime) for v in nn):
        return pd.Series(vals, dtype=object)
    if any(not isinstance(v, str) for v in nn):
        warnings.append(f"Column '{label}' mixes text with numbers or dates, so it was stored as text.")
    return pd.Series([None if v is None else (v if isinstance(v, str) else (v.isoformat() if hasattr(v, "isoformat") else str(v))) for v in vals], dtype=object)


def _frame_from_rows(rows: list[tuple], sheet: str, warnings: list[str]):
    header_i = next((i for i, r in enumerate(rows) if any(not _is_blank(c) for c in r)), None)
    if header_i is None:
        return None
    header, body = rows[header_i], [r for r in rows[header_i + 1:] if any(not _is_blank(c) for c in r)]
    width = max([len(header)] + [len(r) for r in body]) if body else len(header)
    # drop trailing columns that have neither a header nor any data
    keep = [j for j in range(width) if not (_is_blank(header[j] if j < len(header) else None) and all(_is_blank(r[j] if j < len(r) else None) for r in body))]
    if len(keep) > MAX_COLS:
        raise IngestError(f"Sheet '{sheet}' has {len(keep)} columns; the limit is {MAX_COLS}.")
    if not body:
        return None
    labels = [header[j] if j < len(header) and not _is_blank(header[j]) else None for j in keep]
    cols = {j: _column([r[j] if j < len(r) else None for r in body], str(labels[k] or f"column_{k + 1}"), warnings) for k, j in enumerate(keep)}
    return labels, pd.DataFrame({k: cols[j] for k, j in enumerate(keep)})


def _read_xlsx(data: bytes, warnings: list[str]) -> list[tuple[str, list, pd.DataFrame]]:
    _check_zip(data)
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception:
        raise IngestError("The workbook could not be read. It may be corrupted; try re-saving it from Excel.")
    out, cells = [], 0
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            warnings.append(f"Hidden sheet '{ws.title}' was skipped.")
            continue
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append(r)
            cells += len(r)
            if len(rows) > MAX_ROWS + 1:
                raise IngestError(f"Sheet '{ws.title}' has more than {MAX_ROWS:,} rows; split it or remove old data.")
            if cells > MAX_CELLS:
                raise IngestError(f"The workbook has more than {MAX_CELLS:,} cells, which is over the limit.")
        got = _frame_from_rows(rows, ws.title, warnings)
        if got is None:
            warnings.append(f"Sheet '{ws.title}' has no data rows and was skipped.")
            continue
        out.append((ws.title, got[0], got[1]))
        if len(out) > MAX_SHEETS:
            raise IngestError(f"The workbook has more than {MAX_SHEETS} sheets with data; the limit is {MAX_SHEETS}.")
    wb.close()
    return out


def _read_csv(data: bytes, filename: str, warnings: list[str]) -> list[tuple[str, list, pd.DataFrame]]:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            df = pd.read_csv(io.BytesIO(data), nrows=MAX_ROWS + 1, encoding=enc, dtype_backend="numpy_nullable")
            break
        except UnicodeDecodeError:
            continue
        except (pd.errors.EmptyDataError, pd.errors.ParserError) as e:
            raise IngestError(f"The CSV could not be parsed: {e}")
    else:
        raise IngestError("The CSV text encoding is not supported; save it as UTF-8.")
    if len(df) > MAX_ROWS:
        raise IngestError(f"The CSV has more than {MAX_ROWS:,} rows.")
    if len(df.columns) > MAX_COLS:
        raise IngestError(f"The CSV has {len(df.columns)} columns; the limit is {MAX_COLS}.")
    if df.empty:
        raise IngestError("The CSV has no data rows.")
    labels = [None if str(c).startswith("Unnamed:") else c for c in df.columns]
    df.columns = range(len(df.columns))
    return [(Path(filename).stem or "data", labels, df)]


def ingest(filename: str, data: bytes, db_path: Path) -> IngestResult:
    """Parse `data` and write typed tables into a new DuckDB file at db_path. Raises IngestError on any user-fixable problem."""
    ext = Path(filename).suffix.lower()
    if len(data) > MAX_BYTES:
        raise IngestError(f"The file is {len(data) / 1e6:.1f} MB; the limit is {MAX_BYTES // 10**6} MB.")
    if not data:
        raise IngestError("The file is empty.")
    if ext == ".xlsm" or ext == ".xlsb":
        raise IngestError("Macro-enabled / binary workbooks are not accepted. Save a copy as .xlsx and upload that.")
    if ext == ".xls":
        raise IngestError("Legacy .xls files are not supported. Open it in Excel and save as .xlsx.")
    if ext not in (".xlsx", ".csv"):
        raise IngestError("Only .xlsx and .csv files are supported.")
    result = IngestResult()
    sheets = _read_xlsx(data, result.warnings) if ext == ".xlsx" else _read_csv(data, filename, result.warnings)
    if not sheets:
        raise IngestError("No sheet with data was found. The first non-empty row of each sheet is read as the column headers.")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        used_tables: set[str] = set()
        for sheet, labels, df in sheets:
            tname = ident(sheet, "table_data", used_tables)
            used_cols: set[str] = set()
            cols = [ident(l, f"column_{i + 1}", used_cols) if l is not None else ident(f"column_{i + 1}", "column", used_cols) for i, l in enumerate(labels)]
            df.columns = cols
            con.register("df_", df)
            con.execute(f'CREATE TABLE "{tname}" AS SELECT * FROM df_')
            con.unregister("df_")
            types = {c: t for c, t in con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = ?", [tname]).fetchall()}
            result.tables.append(TableMeta(tname, sheet, len(df), [dict(name=c, original="" if l is None else str(l), type=types[c]) for c, l in zip(cols, labels)]))
            if tname != sheet:
                result.warnings.append(f"Sheet '{sheet}' is table '{tname}'.")
    finally:
        con.close()
    return result

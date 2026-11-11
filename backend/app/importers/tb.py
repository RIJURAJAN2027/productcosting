"""Trial-balance importer.

Reads a TB export (GL rows x business-area or cost-centre columns) from Excel
or CSV and converts it to long format ``gl, gl_desc, fs_desc, dim, amount``.

* The header row is detected automatically (the first row that contains a
  "G/L code"-like heading within the first 30 rows).
* Columns are mapped by header *name*, never by position.
* Blank rows, rows without a numeric GL code and total rows are ignored.
* Dimension columns are every other non-empty header.  Columns listed in
  ``ignore_columns`` (helper/total columns) are skipped and reported.
* Per-row check: sum of dimension columns vs the "Total" column.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import csv
import io
import re
from typing import Any, Iterable

import pandas as pd

GL_HEADERS = {"g/l code", "gl code", "g/l account", "gl account", "gl", "account", "g/l", "gl no", "g/l no"}
DESC_HEADERS = {"g/l description", "gl description", "description", "account description", "g/l desc", "gl name"}
FS_HEADERS = {"fs description", "fs desc", "fs line", "financial statement item", "fs item"}
TOTAL_HEADERS = {"total", "grand total", "total amount", "balance"}
TOTAL_ROW = re.compile(r"^(grand\s+)?total\b|^sum\b|^result\b", re.I)


def _key(v: Any) -> str:
    return " ".join(str(v).split()).casefold() if v is not None else ""


def _code(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return 0.0
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s.endswith("-"):           # SAP trailing minus "1,234.00-"
        s, neg = s[:-1], True
    try:
        f = float(s)
    except ValueError:
        return None
    return -f if neg else f


@dataclass
class TbParseResult:
    kind: str                                  # BA | CC
    header_row: int
    gl_col: int
    dims: list[str]
    long: pd.DataFrame                         # gl, gl_desc, fs_desc, dim, amount
    gl_info: dict[str, dict] = field(default_factory=dict)
    total_by_gl: dict[str, float] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    skipped_rows: int = 0

    def summary(self) -> dict:
        return {
            "kind": self.kind, "header_row": self.header_row, "gl_rows": len(self.gl_info),
            "dimension_columns": len(self.dims), "non_zero_cells": int((self.long["amount"] != 0).sum()),
            "total": float(self.long["amount"].sum()), "ignored_columns": self.ignored_columns,
            "skipped_rows": self.skipped_rows, "issues": len(self.issues),
        }


def find_header_row(rows: list[list[Any]], max_scan: int = 30) -> tuple[int, int]:
    for r, row in enumerate(rows[:max_scan]):
        for c, v in enumerate(row):
            if _key(v) in GL_HEADERS:
                return r, c
    raise ValueError("Could not find a header row containing 'G/L Code' in the first 30 rows.")


def parse_rows(rows: list[list[Any]], kind: str = "BA", ignore_columns: Iterable[str] = (),
               keep_columns: Iterable[str] | None = None) -> TbParseResult:
    """Parse a 2-D list of cell values (first row = sheet row 1)."""
    h, gl_col = find_header_row(rows)
    header = rows[h]
    ignore = {_key(x) for x in ignore_columns}
    keep = {_code(x) for x in keep_columns} if keep_columns is not None else None
    desc_col = fs_col = total_col = None
    dims: list[tuple[int, str]] = []
    ignored: list[str] = []
    seen: dict[str, int] = {}
    issues: list[dict] = []
    for c, v in enumerate(header):
        if c == gl_col or v is None or str(v).strip() == "":
            continue
        k = _key(v)
        if k in DESC_HEADERS and desc_col is None:
            desc_col = c
        elif k in FS_HEADERS and fs_col is None:
            fs_col = c
        elif k in TOTAL_HEADERS and total_col is None:
            total_col = c
        else:
            name = _code(v)
            if k in ignore or (keep is not None and name not in keep):
                ignored.append(name)
                continue
            if name in seen:
                issues.append({"level": "error", "type": "duplicate_column",
                               "message": f"Column {name!r} appears more than once; only the first is used."})
                continue
            seen[name] = c
            dims.append((c, name))
    records: list[tuple] = []
    gl_info: dict[str, dict] = {}
    totals: dict[str, float] = {}
    skipped = 0
    for r in range(h + 1, len(rows)):
        row = rows[r]
        gl_raw = row[gl_col] if gl_col < len(row) else None
        gl = _code(gl_raw)
        if not gl or TOTAL_ROW.match(gl) or not re.match(r"^[0-9A-Za-z][0-9A-Za-z_\-./]*$", gl):
            if any(x not in (None, "") for x in row):
                skipped += 1
            continue
        if not any(ch.isdigit() for ch in gl):
            skipped += 1
            continue
        if gl in gl_info:
            issues.append({"level": "error", "type": "duplicate_gl",
                           "message": f"GL {gl} appears on more than one row (row {r + 1}); amounts are added together."})
        gl_info.setdefault(gl, {
            "description": str(row[desc_col]).strip() if desc_col is not None and desc_col < len(row) and row[desc_col] else "",
            "fs_description": str(row[fs_col]).strip() if fs_col is not None and fs_col < len(row) and row[fs_col] else "",
            "row": r + 1,
        })
        row_sum = 0.0
        for c, name in dims:
            v = row[c] if c < len(row) else None
            f = _num(v)
            if f is None:
                issues.append({"level": "error", "type": "non_numeric",
                               "message": f"Row {r + 1}, column {name}: non-numeric value {v!r} treated as 0."})
                continue
            if f != 0.0:
                records.append((gl, gl_info[gl]["description"], gl_info[gl]["fs_description"], name, f))
                row_sum += f
        if total_col is not None:
            t = _num(row[total_col] if total_col < len(row) else None)
            if t is not None:
                totals[gl] = totals.get(gl, 0.0) + t
                if abs(t - row_sum) > 0.5:
                    issues.append({"level": "warning", "type": "row_total_mismatch",
                                   "message": f"GL {gl}: sum of columns {row_sum:,.2f} differs from Total {t:,.2f} "
                                              f"by {row_sum - t:,.2f}.", "gl": gl, "difference": row_sum - t})
    long = pd.DataFrame(records, columns=["gl", "gl_desc", "fs_desc", "dim", "amount"])
    if ignored:
        issues.append({"level": "info", "type": "ignored_columns",
                       "message": f"{len(ignored)} column(s) ignored: {', '.join(ignored[:20])}"})
    return TbParseResult(kind=kind, header_row=h + 1, gl_col=gl_col + 1, dims=[n for _, n in dims], long=long,
                         gl_info=gl_info, total_by_gl=totals, issues=issues, ignored_columns=ignored,
                         skipped_rows=skipped)


def rows_from_worksheet(ws) -> list[list[Any]]:
    return [list(r) for r in ws.iter_rows(values_only=True)]


def read_tb_file(data: bytes, filename: str, kind: str = "BA", sheet: str | None = None,
                 ignore_columns: Iterable[str] = ()) -> TbParseResult:
    """Parse an uploaded TB file (.xlsx/.xlsm or .csv)."""
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
        ws = None
        if sheet and sheet in wb.sheetnames:
            ws = wb[sheet]
        else:
            # prefer a sheet whose name mentions the TB kind, else the first sheet with a GL header
            prefs = [s for s in wb.sheetnames if kind.lower() in s.lower() and "tb" in s.lower()]
            for s in prefs + wb.sheetnames:
                rows = rows_from_worksheet(wb[s])
                try:
                    find_header_row(rows)
                    ws = wb[s]
                    break
                except ValueError:
                    continue
        if ws is None:
            raise ValueError("No worksheet with a 'G/L Code' header was found.")
        rows = rows_from_worksheet(ws)
        wb.close()
    elif name.endswith((".csv", ".txt")):
        text = data.decode("utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|") if text else csv.excel
        rows = [list(r) for r in csv.reader(io.StringIO(text), dialect)]
    else:
        raise ValueError("Unsupported file type; upload .xlsx, .xlsm or .csv")
    return parse_rows(rows, kind=kind, ignore_columns=ignore_columns)


def validate_against_masters(parsed: TbParseResult, known_gls: set[str], known_dims: set[str]) -> list[dict]:
    """Report new GLs / dimension codes missing from the masters, with their amounts."""
    out: list[dict] = []
    df = parsed.long
    new_gl = sorted(set(parsed.gl_info) - known_gls)
    if new_gl:
        amt = df[df["gl"].isin(new_gl)].groupby("gl")["amount"].sum()
        for gl in new_gl:
            out.append({"level": "error" if abs(amt.get(gl, 0.0)) > 0 else "warning", "type": "new_gl",
                        "code": gl, "description": parsed.gl_info[gl]["description"],
                        "amount": float(amt.get(gl, 0.0)),
                        "message": f"GL {gl} ({parsed.gl_info[gl]['description']}) is not in the GL master."})
    new_dim = sorted(set(parsed.dims) - known_dims)
    if new_dim:
        amt = df[df["dim"].isin(new_dim)].groupby("dim")["amount"].sum()
        label = "business area" if parsed.kind == "BA" else "cost centre"
        for d in new_dim:
            out.append({"level": "error" if abs(amt.get(d, 0.0)) > 0 else "warning",
                        "type": "new_ba" if parsed.kind == "BA" else "new_cc", "code": d,
                        "amount": float(amt.get(d, 0.0)),
                        "message": f"Column {d} is not in the {label} master."})
    return out

"""Spreadsheets and templates: reading CSV/Excel uploads, contact keys, Excel export, {field} rendering."""

import csv
import io
import re
import string

import whatsapp as wa
from flask import send_file

from core.config import (
    COLUMN_RE,
    HEADER_ALIASES,
    IMPORT_MAX_COLS,
    MAX_CONTACTS,
    STATUS_FIELDS,
)
from core.validation import Invalid


def norm_header(h):
    return re.sub(r"[^a-z0-9]+", " ", str(h or "").lower()).strip()


def cell_text(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)  # Excel stores phone numbers as floats
    return str(v).strip()


def read_table(filename, data):
    """Return rows (list of dicts) from an uploaded .xlsx or .csv file."""
    name = filename.lower()
    try:
        if name.endswith((".xlsx", ".xlsm")):
            import zipfile

            from openpyxl import load_workbook
            # An .xlsx is a zip: check what it expands to before opening it (a tiny file can unpack to GBs).
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                infos = z.infolist()
                if len(infos) > 200 or sum(i.file_size for i in infos) > 60 * 1024 * 1024:
                    raise Invalid("That spreadsheet is too large to import. Save just the contacts sheet and try again.", "file")
            sheet = load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
            grid = []
            for row in sheet.iter_rows(values_only=True, max_col=IMPORT_MAX_COLS):  # streamed, stops at the cap
                grid.append([cell_text(c) for c in row])
                if len(grid) > MAX_CONTACTS + 1:
                    break
        elif name.endswith(".csv"):
            grid = []
            for row in csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace"))):
                grid.append(row[:IMPORT_MAX_COLS])
                if len(grid) > MAX_CONTACTS + 1:
                    break
        else:
            raise Invalid("Upload an Excel (.xlsx) or CSV file.", "file")
    except Invalid:
        raise
    except Exception:
        raise Invalid("We couldn't read that file. Check it opens in Excel, then try again.", "file")
    grid = [r for r in grid if any(c.strip() for c in r)]
    if len(grid) < 2:
        raise Invalid("That file has no rows under the header.", "file")
    headers = []
    for h in grid[0]:
        n = norm_header(h)
        key = next((f for f, aliases in HEADER_ALIASES.items() if n in aliases), None)
        col = key or n.replace(" ", "_")
        headers.append(col if col and COLUMN_RE.match(col) and col not in STATUS_FIELDS and col != "id" else None)
    if "phone" not in headers and "email" not in headers:
        raise Invalid("We couldn't find a phone or email column. Name one of your columns “Phone” or “Email”.", "file")
    rows = []
    for r in grid[1:]:
        row = {}
        for h, v in zip(headers, r):
            if h and h not in row:
                row[h] = v.strip()[:500]
        rows.append(row)
    return rows


def phone_key(row, cc):
    p = row.get("phone", "")
    return wa.normalise_phone(p, cc) if p.strip() else ""


class Fields(dict):
    def __missing__(self, key):
        return "{" + key + "}"  # leave unknown placeholders untouched


def render(text, recipient, profile):
    values = Fields({k: v for k, v in recipient.items() if k not in STATUS_FIELDS and k != "id" and v})
    values.setdefault("name", "there")
    values.setdefault("company", "your company")
    for k in ("name", "phone", "email"):
        values[f"sender_{k}"] = (profile or {}).get(k, "")
    try:
        return string.Formatter().vformat(text, (), values).strip()
    except (ValueError, IndexError, AttributeError, KeyError):
        return text.strip()


def xlsx_response(headers, rows, filename):
    from openpyxl import Workbook
    wb = Workbook()
    sheet = wb.active
    sheet.append(headers)
    for r in rows:
        sheet.append(r)
        for cell in sheet[sheet.max_row]:
            # Text is always stored as text, never as a formula, whatever it starts with (=, +, -, @, tab…),
            # so a visitor can't plant a live formula in your export through a contact form.
            if isinstance(cell.value, str):
                cell.data_type = "s"
                if cell.value[:1] in ("=", "+", "-", "@", "\t", "\r", "|"):
                    cell.value = "'" + cell.value
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

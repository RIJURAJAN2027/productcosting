"""Helpers for reading Excel formulas (used by the legacy-workbook importer)."""
from __future__ import annotations

import re
from typing import Callable

from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.utils import column_index_from_string, get_column_letter

REF_RE = re.compile(
    r"^(?:(?P<sheet>'(?:[^']|'')+'|[A-Za-z_][\w.]*)!)?\$?(?P<c1>[A-Z]{1,3})\$?(?P<r1>\d+)"
    r"(?::\$?(?P<c2>[A-Z]{1,3})\$?(?P<r2>\d+))?$"
)


def col(letters: str) -> int:
    return column_index_from_string(letters.replace("$", ""))


def letter(index: int) -> str:
    return get_column_letter(index)


def formula_text(value) -> str | None:
    """Return the formula string for a cell value (array formulas included), else None."""
    if value is None:
        return None
    if hasattr(value, "text"):
        return value.text
    if isinstance(value, str) and value.startswith("="):
        return value
    return None


def parse_ref(text: str, default_sheet: str) -> tuple[str, int, int, int, int] | None:
    """Parse 'Sheet'!C5 or C5:D9 -> (sheet, col1, row1, col2, row2)."""
    m = REF_RE.match(text.strip())
    if not m:
        return None
    sheet = m.group("sheet")
    if sheet:
        sheet = sheet[1:-1].replace("''", "'") if sheet.startswith("'") else sheet
    else:
        sheet = default_sheet
    c1, r1 = col(m.group("c1")), int(m.group("r1"))
    c2 = col(m.group("c2")) if m.group("c2") else c1
    r2 = int(m.group("r2")) if m.group("r2") else r1
    return sheet, min(c1, c2), min(r1, r2), max(c1, c2), max(r1, r2)


class Untranslatable(ValueError):
    pass


def translate_arithmetic(formula: str, default_sheet: str,
                         ref_to_expr: Callable[[str, int, int], str | None],
                         allow_functions: tuple[str, ...] = ("SUM",)) -> str:
    """Translate an arithmetic Excel formula (refs, numbers, + - * /, SUM) into the
    engine expression language.  ``ref_to_expr(sheet, col, row)`` returns the
    expression for one cell or None for an empty cell (treated as 0)."""
    tok = Tokenizer(formula)
    out: list[str] = []
    depth_funcs: list[str] = []
    for t in tok.items:
        if t.type == Token.OPERAND and t.subtype == Token.RANGE:
            ref = parse_ref(t.value, default_sheet)
            if ref is None:
                raise Untranslatable(f"Unsupported reference {t.value!r}")
            sheet, c1, r1, c2, r2 = ref
            parts = []
            for c in range(c1, c2 + 1):
                for r in range(r1, r2 + 1):
                    e = ref_to_expr(sheet, c, r)
                    if e is not None:
                        parts.append(e)
            if not parts:
                out.append("0")
            elif len(parts) == 1:
                out.append(parts[0])
            else:
                out.append("(" + " + ".join(parts) + ")")
        elif t.type == Token.OPERAND and t.subtype == Token.NUMBER:
            out.append(t.value)
        elif t.type == Token.OPERAND:
            raise Untranslatable(f"Unsupported operand {t.value!r}")
        elif t.type == Token.FUNC and t.subtype == Token.OPEN:
            name = t.value[:-1].upper().replace("_XLFN.", "")
            if name not in allow_functions:
                raise Untranslatable(f"Unsupported function {name}")
            depth_funcs.append(name)
            out.append(f"{name}(")
        elif t.type == Token.FUNC and t.subtype == Token.CLOSE:
            depth_funcs.pop()
            out.append(")")
        elif t.type == Token.PAREN:
            out.append(t.value)
        elif t.type == Token.SEP and t.subtype == Token.ARG:
            out.append(", ")
        elif t.type in (Token.OP_IN, Token.OP_PRE):
            if t.value not in "+-*/":
                raise Untranslatable(f"Unsupported operator {t.value!r}")
            out.append(f" {t.value} " if t.type == Token.OP_IN else t.value)
        elif t.type == Token.WSPACE:
            continue
        else:
            raise Untranslatable(f"Unsupported token {t.value!r}")
    text = "".join(out).strip()
    return text or "0"


def references(formula: str, default_sheet: str) -> list[tuple[str, int, int, int, int]]:
    """All range references used by a formula."""
    refs = []
    for t in Tokenizer(formula).items:
        if t.type == Token.OPERAND and t.subtype == Token.RANGE:
            r = parse_ref(t.value, default_sheet)
            if r:
                refs.append(r)
    return refs

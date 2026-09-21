"""SVE — Cattolica's exam registration site (secure.unicatt.it/sve).

SVE has no API, but its "Registra voti" page exports an Excel template of the
students enrolled in an appello and imports the same file back with the
"Voto Finale" column filled. The file carries its own column map in a second
sheet (LIST_HEADER, LIST_CONTENT, VOTO, MATRICOLA…), which SVE reads on import;
we read the same map, so a change in the template's layout does not break us.

This module fills that column from Blackboard scores. It never talks to SVE:
the upload, the error check and the signature stay with the instructor.
"""

from __future__ import annotations

import math
import pathlib
from dataclasses import dataclass, field
from typing import Any

import openpyxl

from .client import BlackboardError

ESITI = ["30 e lode", "30", "29", "28", "27", "26", "25", "24", "23", "22", "21", "20", "19", "18",
         "approvato", "17", "16", "15", "14", "13", "12", "11", "10", "9", "8", "7", "6", "5", "4", "3",
         "2", "1", "0", "respinto", "non approvato", "non sufficiente", "ritirato"]
NOT_PASSED = {"non sufficiente", "respinto", "non approvato", "ritirato"}


def esito(score: float | None, *, not_passed: str = "non sufficiente", lode_from: float = 31,
          pass_mark: float = 18) -> str | None:
    """Blackboard score → SVE outcome string. None stays None (no grade to register).

    Rounded half-up to the integer mark; >= `lode_from` is "30 e lode"; below
    `pass_mark` becomes `not_passed` (one of the SVE failing outcomes).
    """
    if score is None:
        return None
    if not_passed not in NOT_PASSED:
        raise BlackboardError(f"not_passed must be one of {sorted(NOT_PASSED)}")
    if score < 0 or score > 33:
        raise BlackboardError(f"score {score} is outside the 0–30 (31 = lode) range SVE can take")
    if score >= lode_from:
        return "30 e lode"
    mark = math.floor(score + 0.5)
    if mark < pass_mark:
        return not_passed
    return str(min(mark, 30))


@dataclass
class Layout:
    header_row: int
    first_row: int
    columns: dict[str, int] = field(default_factory=dict)   # key → 1-based column
    meta: dict[str, str] = field(default_factory=dict)

    def col(self, key: str) -> int:
        if key not in self.columns:
            raise BlackboardError(f"template has no {key} column in its config sheet")
        return self.columns[key]

    @property
    def appello(self) -> str | None:
        return self.meta.get("ID_APPELLO")

    @property
    def insegnamento(self) -> str | None:
        return self.meta.get("COD_INSEGNAMENTO")


def read_layout(wb) -> Layout:
    """Read the map SVE keeps in the template's second sheet.

    That sheet holds three things, and telling them apart matters: rows above
    LIST_HEADER map the metadata (key, row, column of sheet 1, both 0-based);
    the rows between LIST_HEADER and LIST_CONTENT map the data columns (key,
    blank, column); anything after that (UNLOCK_START/END) is a cell range, not
    a column. Reading positions from here rather than hard-coding them is what
    keeps this working if SVE moves a column.
    """
    if len(wb.worksheets) < 2:
        raise BlackboardError("not an SVE template: expected a second sheet with the column map")
    cfg = wb.worksheets[1]
    rows = [(str(r[0]).strip() if r[0] is not None else None, r[1], r[2])
            for r in cfg.iter_rows(min_row=1, max_col=3, values_only=True)]
    marker = {key: i for i, (key, _, _) in enumerate(rows) if key in ("LIST_HEADER", "LIST_CONTENT")}
    if "LIST_HEADER" not in marker or "LIST_CONTENT" not in marker:
        raise BlackboardError("template config sheet lacks LIST_HEADER / LIST_CONTENT")
    hi, ci = marker["LIST_HEADER"], marker["LIST_CONTENT"]
    header = int(rows[hi][1]) + 1
    content = int(rows[ci][1]) + 1

    sheet = wb.worksheets[0]
    meta: dict[str, str] = {}
    for key, row0, col0 in rows[:hi]:
        if key and row0 is not None and col0 is not None:
            value = sheet.cell(int(row0) + 1, int(col0) + 1).value
            if value is not None:
                meta[key] = str(value).strip()
    columns: dict[str, int] = {}
    for key, row0, col0 in rows[hi + 1:ci]:
        if key and row0 is None and col0 is not None:
            columns[key] = int(col0) + 1
    return Layout(header_row=header, first_row=content, columns=columns, meta=meta)


def _matricola(value: Any) -> str:
    """Excel may hold a student number as text, int or float; SVE and Blackboard use text."""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def fill(template: str | pathlib.Path, scores: dict[str, float | None], out: str | pathlib.Path, *,
         exam_date: str = "", not_passed: str = "non sufficiente", lode_from: float = 31,
         overwrite: bool = False) -> dict[str, Any]:
    """Write Voto Finale for every enrolled student found in `scores` (keyed by matricola).

    Students enrolled in SVE with no Blackboard score are left blank and listed;
    matricole graded in Blackboard but not enrolled in the appello are listed too.
    Existing outcomes in the template are refused unless overwrite=True.
    """
    src = pathlib.Path(template).expanduser()
    if not src.is_file():
        raise BlackboardError(f"no such template: {src}")
    wb = openpyxl.load_workbook(src)
    lay = read_layout(wb)
    ws = wb.worksheets[0]
    c_mat, c_voto, c_name = lay.col("MATRICOLA"), lay.col("VOTO"), lay.columns.get("COGNOME_NOME")
    c_date = lay.columns.get("DATA_SVOLGIMENTO_ESAME")

    seen: set[str] = set()
    filled = 0
    rows = 0
    counts: dict[str, int] = {}
    missing: list[dict] = []
    for r in range(lay.first_row, ws.max_row + 1):
        mat = ws.cell(r, c_mat).value
        if mat is None or str(mat).strip() == "":
            continue
        mat = _matricola(mat)
        rows += 1
        seen.add(mat)
        current = ws.cell(r, c_voto).value
        if current not in (None, "") and not overwrite:
            raise BlackboardError(f"row {r} (matricola {mat}) already has esito {current!r}; "
                                  f"pass overwrite=True to replace it")
        e = esito(scores.get(mat), not_passed=not_passed, lode_from=lode_from)
        if e is None:
            missing.append({"matricola": mat, "name": ws.cell(r, c_name).value if c_name else None})
            continue
        ws.cell(r, c_voto, e)
        if exam_date and c_date:
            ws.cell(r, c_date, exam_date)
        counts[e] = counts.get(e, 0) + 1
        filled += 1

    dst = pathlib.Path(out).expanduser()
    dst.parent.mkdir(parents=True, exist_ok=True)
    wb.save(dst)
    return {
        "path": str(dst), "appello": lay.appello, "insegnamento": lay.insegnamento,
        "rows": rows, "filled": filled, "esiti": counts,
        "no_grade_in_blackboard": missing,
        "graded_but_not_enrolled": sorted(m for m, s in scores.items() if s is not None and m not in seen),
        "next": "SVE > Registra voti > Caricamento voti Excel > Importa esiti da XLS, check the Errore "
                "column, then Inoltro alla firma.",
    }

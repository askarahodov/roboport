"""Bounded, formula-free synthetic SKU XLSX import/export (single plain worksheet).

Reuses the project preflight's hostile-OOXML checks. Never reads macros, links,
formulas, hidden sheets or personal information. Not a general Excel renderer.
"""
from __future__ import annotations

from io import BytesIO
from xml.sax.saxutils import escape
import re
from zipfile import ZIP_DEFLATED, ZipFile

from scripts.auto_synthetic_xlsx import (
    _rows_from_xlsx, SyntheticWorkbookDenied,
)

HEADERS=("article","category","uom","barcode","brand",
         "purchaseMinor","saleMinor","analogArticle")
MAX_BYTES=128_000
MAX_ROWS=100
ARTICLE=re.compile(r"^[A-Z0-9][A-Z0-9._/-]{1,79}$")
CATEGORY=re.compile(r"^[A-Z0-9][A-Z0-9_-]{1,31}$")
BARCODE=re.compile(r"^[0-9]{8,32}$")


class CatalogWorkbookInvalid(ValueError):
    pass


def clean_row(values: list[str]) -> dict:
    if len(values)!=len(HEADERS) or any(type(v) is not str for v in values):
        raise CatalogWorkbookInvalid("SCHEMA")
    article,category,uom,barcode,brand,purchase,sale,analog=values
    if (not ARTICLE.fullmatch(article) or
        not CATEGORY.fullmatch(category) or uom not in ("PCS","L","KG") or
        (barcode and not BARCODE.fullmatch(barcode)) or
        (brand and (len(brand)>80 or brand!=brand.strip()
                    or brand.lstrip().startswith(("=","+","-","@")))) or
        (analog and (not ARTICLE.fullmatch(analog) or analog==article))):
        raise CatalogWorkbookInvalid("FIELDS")
    def money(value):
        if not re.fullmatch(r"(0|[1-9][0-9]{0,9})",value):
            raise CatalogWorkbookInvalid("MONEY")
        n=int(value)
        if n>1_000_000_000:
            raise CatalogWorkbookInvalid("MONEY")
        return n
    return {"article":article,"category":category,"uom":uom,
            "barcode":barcode or None,"brand":brand or None,
            "purchaseMinor":money(purchase),"saleMinor":money(sale),
            "analogArticle":analog or None}


def parse_catalog_xlsx(raw: bytes) -> list[dict]:
    if type(raw) is not bytes or not 0<len(raw)<=MAX_BYTES:
        raise CatalogWorkbookInvalid("FILE_LIMIT")
    try:
        grid=_rows_from_xlsx(raw,HEADERS)
    except SyntheticWorkbookDenied:
        raise CatalogWorkbookInvalid("WORKBOOK_UNSAFE_OR_INVALID") from None
    if not 1<=len(grid)-1<=MAX_ROWS:
        raise CatalogWorkbookInvalid("ROW_LIMIT")
    rows=[clean_row(row) for row in grid[1:]]
    if (len({x["article"] for x in rows})!=len(rows) or
        len([x["barcode"] for x in rows if x["barcode"]])!=
        len({x["barcode"] for x in rows if x["barcode"]})):
        raise CatalogWorkbookInvalid("DUPLICATE")
    return rows


def export_catalog_xlsx(rows: list[dict]) -> bytes:
    if not 0<=len(rows)<=10000:
        raise CatalogWorkbookInvalid("EXPORT_CAP")
    parts=[]
    for idx,row in enumerate([list(HEADERS)]+[[
        str(item["article"]),str(item["category"]),str(item["uom"]),
        str(item.get("barcode") or ""),str(item.get("brand") or ""),
        str(item["purchaseMinor"]),str(item["saleMinor"]),
        str(item.get("analogArticle") or "")
    ] for item in rows],start=1):
        cells=[]
        for col,value in enumerate(row):
            addr=chr(65+col)+str(idx)
            if idx>1 and col in (5,6):
                cells.append(f'<c r="{addr}"><v>{int(value)}</v></c>')
            else:
                # Explicit inline string cell: never a formula. Existing
                # server-side validation disallows dangerous prefixes for
                # imported article/category/brand fields.
                cells.append(f'<c r="{addr}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        parts.append(f'<row r="{idx}">{"".join(cells)}</row>')
    sheet='<?xml version="1.0" encoding="utf-8"?>'+(
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetData>'+''.join(parts)+'</sheetData></worksheet>')
    files={
       '[Content_Types].xml':'<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>',
       '_rels/.rels':'<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>',
       'xl/workbook.xml':'<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Каталог" sheetId="1" r:id="rId1"/></sheets></workbook>',
       'xl/_rels/workbook.xml.rels':'<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>',
       'xl/worksheets/sheet1.xml':sheet,
    }
    stream=BytesIO()
    with ZipFile(stream,"w",compression=ZIP_DEFLATED,compresslevel=7) as archive:
        for name,value in files.items():
            archive.writestr(name,value)
    return stream.getvalue()

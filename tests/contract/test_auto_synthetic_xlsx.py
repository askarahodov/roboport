"""Offline, synthetic XLSX inputs for both existing vehicle and service CSV paths."""
import csv
import io
import zipfile
from uuid import uuid4
from xml.sax.saxutils import escape

import pytest

from scripts.auto_synthetic_xlsx import SyntheticWorkbookDenied, convert_synthetic_xlsx
from scripts.auto_vehicle_migration_preflight import COLUMNS, inspect_vehicle_csv
from roboport_api.auto_service_import import FIELDS, inspect_synthetic_services

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def workbook(path, headers, row, *, formula=False, extra_sheet=False, shared=False):
    sheet_rows = []
    for rownum, fields in enumerate((headers, row), 1):
        cells = []
        for i, value in enumerate(fields):
            column = chr(65 + i)
            ref = f"{column}{rownum}"
            if formula and rownum == 2 and i == 0:
                cells.append(f'<c r="{ref}"><f>1+1</f><v>2</v></c>')
            elif shared:
                index = (rownum - 1) * len(headers) + i
                cells.append(f'<c r="{ref}" t="s"><v>{index}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        sheet_rows.append(f'<row r="{rownum}">{"".join(cells)}</row>')
    sheet = f'<worksheet xmlns="{NS}"><sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    sheets = '<sheet name="Import" sheetId="1"/>'
    if extra_sheet:
        sheets += '<sheet name="Other" sheetId="2"/>'
    manifest = f'<workbook xmlns="{NS}"><sheets>{sheets}</sheets></workbook>'
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as book:
        book.writestr("[Content_Types].xml", "<Types/>")
        book.writestr("xl/workbook.xml", manifest)
        book.writestr("xl/worksheets/sheet1.xml", sheet)
        if shared:
            all_strings = [*headers, *row]
            strings = f'<sst xmlns="{NS}">' + "".join(
                f"<si><t>{escape(v)}</t></si>" for v in all_strings) + "</sst>"
            book.writestr("xl/sharedStrings.xml", strings)


def test_vehicle_workbook_to_canonical_preflight(tmp_path):
    org, loc = uuid4(), uuid4()
    source, output = tmp_path / "vehicle.xlsx", tmp_path / "vehicle.csv"
    values = ("VEHICLE", "SRC-1", str(org), str(loc),
              "1HGCM82633A004381", "SYNTHETIC", "TEST CAR", "DEMO")
    workbook(source, COLUMNS, values)
    result = convert_synthetic_xlsx(source, output, kind="VEHICLE")
    assert result["ok"] and result["rows"] == 1 and result["writes"] == 0
    assert inspect_vehicle_csv(output)["ok"]
    assert str(org) not in str(result) and values[4] not in str(result)
    assert output.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SyntheticWorkbookDenied, match="OUTPUT_UNAVAILABLE"):
        convert_synthetic_xlsx(source, output, kind="VEHICLE")


def test_service_workbook_shared_strings_to_service_preflight(tmp_path):
    source, output = tmp_path / "service.xlsx", tmp_path / "service.csv"
    row = ("SERVICE", "DEMO", "SRV-1", "BRAKE-TEST", "SYNTHETIC BRAKE TEST", "30")
    workbook(source, FIELDS, row, shared=True)
    result = convert_synthetic_xlsx(source, output, kind="SERVICE")
    assert result["rows"] == 1
    assert inspect_synthetic_services(output, expected_sha256=result["sha256"])["ok"]
    with output.open(encoding="utf-8", newline="") as stream:
        assert tuple(next(csv.reader(stream))) == FIELDS


@pytest.mark.parametrize("options,code", [
    ({"formula": True}, "FORMULA"),
    ({"extra_sheet": True}, "SHEETS"),
])
def test_rejects_unsafe_excel_without_output(tmp_path, options, code):
    source, output = tmp_path / "unsafe.xlsx", tmp_path / "out.csv"
    workbook(source, FIELDS,
             ("SERVICE", "DEMO", "S-1", "OIL-CHANGE", "SYNTHETIC", "30"),
             **options)
    with pytest.raises(SyntheticWorkbookDenied, match=code):
        convert_synthetic_xlsx(source, output, kind="SERVICE")
    assert not output.exists()


def test_rejects_real_owner_columns_and_invalid_vehicle_without_output(tmp_path):
    source, output = tmp_path / "owner.xlsx", tmp_path / "out.csv"
    org, loc = uuid4(), uuid4()
    workbook(source, (*COLUMNS, "owner"),
             ("VEHICLE", "SRC-1", str(org), str(loc),
              "1HGCM82633A004382", "SYNTHETIC", "TEST CAR", "DEMO", "PERSON"))
    with pytest.raises(SyntheticWorkbookDenied, match="SCHEMA"):
        convert_synthetic_xlsx(source, output, kind="VEHICLE")
    assert not output.exists()
    workbook(source, COLUMNS, ("VEHICLE", "SRC-1", str(org), str(loc),
             "1HGCM82633I004382", "SYNTHETIC", "TEST CAR", "DEMO"))
    with pytest.raises(SyntheticWorkbookDenied, match="PREFLIGHT"):
        convert_synthetic_xlsx(source, output, kind="VEHICLE")
    assert not output.exists()


def test_rejects_symlink_and_malicious_zip(tmp_path):
    source, output = tmp_path / "clean.xlsx", tmp_path / "out.csv"
    workbook(source, FIELDS, ("SERVICE", "DEMO", "S-1", "OIL-CHANGE", "SYNTHETIC", "30"))
    link = tmp_path / "link.xlsx"
    link.symlink_to(source)
    with pytest.raises(SyntheticWorkbookDenied, match="UNAVAILABLE"):
        convert_synthetic_xlsx(link, output, kind="SERVICE")
    with zipfile.ZipFile(source, "a") as book:
        book.writestr("../bad", "x")
    with pytest.raises(SyntheticWorkbookDenied, match="WORKBOOK_UNSAFE"):
        convert_synthetic_xlsx(source, output, kind="SERVICE")
    assert not output.exists()

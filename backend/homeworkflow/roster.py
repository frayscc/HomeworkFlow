from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import BinaryIO
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class Student:
    number: str
    name: str


@dataclass(frozen=True)
class Roster:
    class_name: str
    students: tuple[Student, ...]

    def with_class_name(self, class_name: str) -> "Roster":
        value = class_name.strip()
        if not value:
            raise ValueError("班级名称不能为空")
        return replace(self, class_name=value)


def _validate(class_name: str, students: list[Student]) -> Roster:
    if not 1 <= len(students) <= 49:
        raise ValueError(f"当前 A5 周表支持 1-49 名学生，实际为 {len(students)} 人")
    numbers = [student.number for student in students]
    if len(set(numbers)) != len(numbers):
        raise ValueError("花名册中存在重复学号")
    if any(not student.number or not student.name for student in students):
        raise ValueError("学号和姓名不能为空")
    return Roster(class_name=class_name.strip() or "未命名班级", students=tuple(students))


def parse_csv_roster(content: bytes, filename: str = "roster.csv") -> Roster:
    text = content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [[cell.strip() for cell in row] for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("CSV 花名册为空")
    header = rows[0]
    if "学号" in header and "姓名" in header:
        number_col, name_col, data = header.index("学号"), header.index("姓名"), rows[1:]
    else:
        number_col, name_col, data = 0, 1, rows
    students = [Student(row[number_col], row[name_col]) for row in data
                if len(row) > max(number_col, name_col) and row[number_col] and row[name_col]]
    return _validate(Path(filename).stem, students)


def parse_text_roster(text: str, class_name: str = "未命名班级") -> Roster:
    """Parse one student per line as ``number<space-or-tab>name``."""
    students: list[Student] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"花名册第 {line_number} 行格式无效，应为“学号 姓名”")
        number, name = (part.strip() for part in parts)
        if number in {"学号", "编号"} and name in {"姓名", "学生姓名"}:
            continue
        students.append(Student(number, name))
    if not students:
        raise ValueError("粘贴的花名册为空")
    return _validate(class_name, students)


def _column_number(reference: str) -> int:
    match = re.match(r"[A-Z]+", reference)
    if not match:
        raise ValueError(f"无效单元格引用：{reference}")
    result = 0
    for letter in match.group(0):
        result = result * 26 + ord(letter) - ord("A") + 1
    return result - 1


def _xlsx_rows(source: bytes | BinaryIO) -> dict[str, list[list[object]]]:
    stream = io.BytesIO(source) if isinstance(source, bytes) else source
    ns = {
        "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    with zipfile.ZipFile(stream) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in item.findall(".//m:t", ns))
                      for item in root.findall("m:si", ns)]
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        result: dict[str, list[list[object]]] = {}
        for sheet in workbook.findall("m:sheets/m:sheet", ns):
            target = targets[sheet.attrib[f"{{{ns['r']}}}id"]]
            member = target.lstrip("/") if target.startswith("/xl/") else "xl/" + target.lstrip("/")
            root = ET.fromstring(archive.read(member))
            rows: list[list[object]] = []
            for row in root.findall(".//m:sheetData/m:row", ns):
                values: list[object] = []
                for cell in row.findall("m:c", ns):
                    index = _column_number(cell.attrib["r"])
                    values.extend([None] * (index + 1 - len(values)))
                    cell_type = cell.attrib.get("t")
                    value_node = cell.find("m:v", ns)
                    if cell_type == "inlineStr":
                        value: object = "".join(node.text or "" for node in cell.findall(".//m:t", ns))
                    elif value_node is None:
                        value = None
                    elif cell_type == "s":
                        value = shared[int(value_node.text)]
                    elif cell_type in {"str", "b"}:
                        value = value_node.text
                    else:
                        number = float(value_node.text)
                        value = int(number) if number.is_integer() else number
                    values[index] = value
                rows.append(values)
            result[sheet.attrib["name"]] = rows
        return result


def parse_xlsx_roster(content: bytes, filename: str = "roster.xlsx") -> Roster:
    sheets = _xlsx_rows(content)
    candidates = []
    for preferred in ("小组制", "Sheet2"):
        if preferred in sheets:
            candidates.append((preferred, sheets[preferred]))
    candidates.extend((name, rows) for name, rows in sheets.items() if name not in {item[0] for item in candidates})

    students: list[Student] = []
    for _, rows in candidates:
        if not rows:
            continue
        header_index = next((index for index, row in enumerate(rows)
                             if "学号" in row and "姓名" in row), None)
        if header_index is not None:
            header = rows[header_index]
            number_col, name_col = header.index("学号"), header.index("姓名")
            data = rows[header_index + 1:]
        elif len(rows[0]) >= 2:
            number_col, name_col, data = 0, 1, rows
        else:
            continue
        found = []
        for row in data:
            if len(row) > max(number_col, name_col) and row[number_col] not in (None, "") and row[name_col] not in (None, ""):
                found.append(Student(str(row[number_col]).strip(), str(row[name_col]).strip()))
        if found:
            students = found
            break
    if not students:
        raise ValueError("Excel 中未找到可用的“学号、姓名”花名册")

    class_name = Path(filename).stem
    pattern = re.compile(r"(初[一二三]|高[一二三]|[七八九]年级)[（(]?\d+[）)]?班")
    for rows in sheets.values():
        for row in rows[:10]:
            for value in row:
                if isinstance(value, str):
                    match = pattern.search(value)
                    if match:
                        class_name = match.group(0).replace("(", "（").replace(")", "）")
                        return _validate(class_name, students)
    return _validate(class_name, students)


def parse_roster(content: bytes, filename: str) -> Roster:
    suffix = Path(filename).suffix.lower()
    if suffix == ".xlsx":
        return parse_xlsx_roster(content, filename)
    if suffix in {".csv", ".txt"}:
        return parse_csv_roster(content, filename)
    raise ValueError("花名册仅支持 XLSX 或 UTF-8 CSV")

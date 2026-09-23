from homeworkflow.roster import parse_csv_roster, parse_text_roster


def test_csv_preserves_non_contiguous_student_numbers():
    roster = parse_csv_roster("学号,姓名\n1,学生甲\n2,学生乙\n4,学生丙\n".encode(), "十五班.csv")
    assert roster.class_name == "十五班"
    assert [student.number for student in roster.students] == ["1", "2", "4"]


def test_duplicate_numbers_are_rejected():
    try:
        parse_csv_roster("学号,姓名\n1,学生甲\n1,学生乙\n".encode(), "roster.csv")
    except ValueError as exc:
        assert "重复学号" in str(exc)
    else:
        raise AssertionError("duplicate number was accepted")


def test_pasted_roster_accepts_spaces_tabs_and_preserves_leading_zeroes():
    roster = parse_text_roster("01 张三\n02\t李四\n03    王五\n", "初二（3）班")
    assert roster.class_name == "初二（3）班"
    assert [(item.number, item.name) for item in roster.students] == [
        ("01", "张三"), ("02", "李四"), ("03", "王五")
    ]


def test_pasted_roster_reports_invalid_line_number():
    try:
        parse_text_roster("01 张三\n02\n", "演示班")
    except ValueError as exc:
        assert "第 2 行" in str(exc)
    else:
        raise AssertionError("invalid pasted row was accepted")

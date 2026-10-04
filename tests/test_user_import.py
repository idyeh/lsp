"""Bulk account import from a CSV template."""

import csv
import html
import io
import re

import pytest
from sqlalchemy import func, select

from app.enums import Role
from app.models import User
from app.services import user_import
from app.services.user_import import UserImportError, import_users
from tests.helpers import visible_text

HEADER = "username,display_name,role,year_group,password\n"


def csv_bytes(text, encoding="utf-8"):
    return text.encode(encoding)


def problems_of(db, text, encoding="utf-8"):
    with pytest.raises(UserImportError) as exc:
        import_users(db, csv_bytes(text, encoding))
    return exc.value.problems


def user_count(db):
    return db.scalar(select(func.count()).select_from(User))


# --- the service --------------------------------------------------------------------------------


def test_creates_accounts_with_all_fields(db):
    accounts = import_users(db, csv_bytes(HEADER + "Alice,Alice Wang,student,UG2,secret-pass-1\nbob,Bob Li,organiser,PG1,secret-pass-2\n"))
    assert [(a.username, a.role, a.year_group) for a in accounts] == [("alice", Role.student, "UG2"), ("bob", Role.organiser, "PG1")]
    alice = db.scalar(select(User).where(User.username == "alice"))
    assert (alice.display_name, alice.is_active) == ("Alice Wang", True)
    assert alice.password_hash.startswith("$argon2")
    assert all(a.password is None and not a.generated for a in accounts)  # supplied passwords are never echoed back


def test_blank_passwords_are_generated_and_work(db):
    (account,) = import_users(db, csv_bytes(HEADER + "carol,Carol,,,\n"))
    assert account.generated and len(account.password) == user_import.GENERATED_PASSWORD_LENGTH
    assert set(account.password) <= set(user_import.PASSWORD_ALPHABET)
    from app.services import users

    assert users.authenticate(db, "carol", account.password).username == "carol"


def test_generated_passwords_are_different_each_time(db):
    accounts = import_users(db, csv_bytes(HEADER + "".join(f"u{i},User {i},,,\n" for i in range(30))))
    assert len({a.password for a in accounts}) == 30


def test_optional_columns_may_be_left_out_and_columns_reordered(db):
    accounts = import_users(db, csv_bytes("display_name,USERNAME\nDan Zhao,dan\n"))
    assert (accounts[0].username, accounts[0].role, accounts[0].year_group) == ("dan", Role.student, None)


def test_header_names_are_forgiving(db):
    accounts = import_users(db, csv_bytes(" Username , Display Name , Year Group \nerin,Erin,UG3\n"))
    assert accounts[0].year_group == "UG3" and accounts[0].display_name == "Erin"


def test_blank_rows_and_missing_trailing_cells_are_ignored(db):
    accounts = import_users(db, csv_bytes(HEADER + "frank,Frank\n\n,,,,\ngina,Gina,student\n"))
    assert [a.username for a in accounts] == ["frank", "gina"]


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gb18030"])
def test_chinese_names_in_utf8_and_excel_gbk_files(db, encoding):
    (account,) = import_users(db, csv_bytes(HEADER + "wang,王小明,student,本科二年级,\n", encoding))
    assert account.display_name == "王小明" and account.year_group == "本科二年级"
    assert db.scalar(select(User.display_name).where(User.username == "wang")) == "王小明"


@pytest.mark.parametrize("delimiter", [";", "\t"])
def test_semicolon_and_tab_separated_files(db, delimiter):
    text = delimiter.join(["username", "display_name", "role"]) + "\n" + delimiter.join(["hao", "Hao", "student"]) + "\n"
    assert import_users(db, csv_bytes(text))[0].username == "hao"


def test_quoted_cells_with_commas(db):
    (account,) = import_users(db, csv_bytes(HEADER + 'ivy,"Ivy, the Third",student,,\n'))
    assert account.display_name == "Ivy, the Third"


# --- problems: the whole file is rejected and nothing is created ----------------------------------------


def test_every_problem_is_reported_with_its_row_and_nothing_is_created(db, student):
    before = user_count(db)
    text = HEADER + "\n".join([
        "good,Good Person,,,",                    # row 2: fine
        "bad name,Has Space,,,",                  # row 3
        "nodisplay,,,,",                          # row 4
        "weak,Weak,,,short",                      # row 5
        "role,Role,teacher,,",                    # row 6
        "student1,Taken,,,",                      # row 7: already exists (the fixture user)
        "good,Repeated,,,",                       # row 8: repeated in the file
        "yg,YG,,thisyeargroupiswaytoolong,",      # row 9
    ]) + "\n"
    problems = problems_of(db, text + "unquoted,Has,student,UG1,a,b\n")  # row 10: more values than columns
    assert [p.row for p in problems] == [3, 4, 5, 6, 7, 8, 9, 10]  # reported in file order
    assert user_count(db) == before  # even the valid row 2 was not created
    by_row = {p.row: p for p in problems}
    assert "without spaces" in by_row[3].template and "Display name" in by_row[4].template
    assert by_row[5].params == {"min": 8, "max": 256} and by_row[6].params == {"role": "teacher"}
    assert by_row[7].params == {"username": "student1"} and "more than once" in by_row[8].template


def test_extra_blank_cells_are_fine_but_extra_values_are_an_error_not_silently_dropped(db):
    assert import_users(db, csv_bytes(HEADER + "ok,Fine,,,pass-word-1,,\n"))[0].username == "ok"  # spreadsheet trailing commas
    problems = problems_of(db, HEADER + "li,Li,student,UG2,abc,def\n")  # an unquoted comma in the password
    assert [p.row for p in problems] == [2] and "more values" in problems[0].template
    assert user_count(db) == 1  # only "ok" from the first call


def test_header_problems(db):
    assert [p.template for p in problems_of(db, "username\nx\n")] == ["The file has no '{column}' column."]
    unknown = problems_of(db, "username,display_name,email\nx,X,x@y\n")
    assert unknown[0].params == {"column": "email"}
    dup = problems_of(db, "username,display_name,username\nx,X,y\n")
    assert dup[0].params == {"column": "username"}


@pytest.mark.parametrize(
    "raw, fragment",
    [
        (b"", "empty"),
        (b"\n\n", "empty"),
        (HEADER.encode(), "no accounts"),
        (b"\xff\xfe\x00 not text \xc3\x28" * 3, "could not be read"),
        (b"x" * (user_import.MAX_BYTES + 1), "too large"),
    ],
)
def test_unusable_files(db, raw, fragment):
    with pytest.raises(UserImportError) as exc:
        import_users(db, raw)
    assert fragment in exc.value.problems[0].template and exc.value.problems[0].row is None


def test_too_many_rows(db):
    rows = "".join(f"u{i},U{i}\n" for i in range(user_import.MAX_ROWS + 1))
    problems = problems_of(db, "username,display_name\n" + rows)
    assert "more than {max}" in problems[0].template and user_count(db) == 0


def test_the_maximum_size_is_accepted(db):
    rows = "".join(f"u{i},U{i}\n" for i in range(user_import.MAX_ROWS))
    assert len(import_users(db, csv_bytes("username,display_name\n" + rows))) == user_import.MAX_ROWS


# --- the files we produce ---------------------------------------------------------------------------------


def test_the_template_is_exactly_the_documented_header_and_can_be_read_back(db):
    text = user_import.template_csv()
    assert text.startswith("﻿") and text.lstrip("﻿").strip().split(",") == user_import.TEMPLATE_COLUMNS
    with pytest.raises(UserImportError) as exc:  # an unedited template has no accounts
        import_users(db, text.encode("utf-8"))
    assert "no accounts" in exc.value.problems[0].template


def test_credentials_csv_round_trips_and_is_safe_in_a_spreadsheet(db):
    accounts = import_users(db, csv_bytes(HEADER + '=cmd,"=1+1",student,,\n+x,"@SUM(A1)",,,\n'))
    text = user_import.credentials_csv(accounts)
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    assert rows[0] == ["username", "display_name", "role", "year_group", "password"]
    assert rows[1][:2] == ["'=cmd", "'=1+1"] and rows[2][:2] == ["'+x", "'@SUM(A1)"]  # formulas are neutralised
    assert rows[1][4] == accounts[0].password


# --- the web page --------------------------------------------------------------------------------------------


def upload(b, content, name="accounts.csv", encoding="utf-8", page="/organiser/users"):
    data = content.encode(encoding) if isinstance(content, str) else content
    return b.post("/organiser/users/import", files={"file": (name, data, "text/csv")}, page=page)


def test_the_template_can_be_downloaded(browser, organiser):
    response = browser(organiser).get("/organiser/users/template.csv")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert 'attachment; filename="accounts-template.csv"' in response.headers["content-disposition"]
    assert response.content.decode("utf-8-sig").strip() == "username,display_name,role,year_group,password"


def test_organiser_imports_accounts_and_sees_generated_passwords_once(browser, db, organiser):
    b = browser(organiser)
    response = upload(b, HEADER + "amy,Amy Chen,student,UG2,\nben,Ben Xu,student,UG3,own-password-1\n")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Accounts created: 2" in response.text and "amy" in response.text and "from your file" in response.text
    generated = db.scalar(select(User).where(User.username == "amy"))
    shown = re.search(r'<code class="password">([^<]+)</code>', response.text).group(1)
    assert browser().login(generated, shown).headers["location"] == "/"  # the password on the page really works
    assert "own-password-1" not in response.text  # a password the organiser typed is not repeated
    assert "data:text/csv" in response.text and "amy" in response.text
    assert shown not in b.get("/organiser/users").text  # and it is not shown again
    assert browser().login(db.scalar(select(User).where(User.username == "ben")), "own-password-1").headers["location"] == "/"


def test_import_errors_are_listed_and_nothing_is_created(browser, db, organiser):
    b = browser(organiser)
    before = user_count(db)
    response = upload(b, HEADER + "ok,Fine,,,\nbad name,Bad,,,\norganiser,Dup,,,\n")
    assert response.status_code == 422
    assert "Nothing was imported" in response.text
    text = response.text
    assert "Row 3:" in text and "Row 4:" in text and "Row 2:" not in text
    assert "Username &#39;organiser&#39; is already taken." in text
    assert user_count(db) == before


def test_missing_file_and_bad_files_get_clear_messages(browser, organiser):
    b = browser(organiser)
    none = b.post("/organiser/users/import", data={}, page="/organiser/users")
    assert none.status_code == 422 and "Choose a CSV file" in none.text
    assert "The file has no 'username' column." in visible_text(html.unescape(upload(b, "display_name\nx\n").text))
    assert "The file is empty." in visible_text(upload(b, "").text)


def test_import_messages_are_translated(browser, organiser):
    b = browser(organiser, headers={"Accept-Language": "zh-CN"})
    text = upload(b, HEADER + "bad name,Bad,,,\n").text
    assert "没有导入任何账号" in text and "第 2 行：" in text and "不能包含空格" in text
    ok = upload(b, "username,display_name\nzhang,张三\n")
    assert "账号已创建" in ok.text and "来自你的文件" not in ok.text and "张三" in ok.text


def test_students_cannot_import_or_download_the_template(browser, student):
    b = browser(student)
    assert b.get("/organiser/users/template.csv").status_code == 403
    assert upload(b, HEADER + "x,X,,,\n", page="/topics").status_code == 403  # a student's token, from a page they may open


def test_importing_requires_the_csrf_token(browser, organiser):
    b = browser(organiser)
    response = b.client.post("/organiser/users/import", files={"file": ("a.csv", HEADER.encode(), "text/csv")})
    assert response.status_code == 403


def test_imported_students_work_like_any_other_account(browser, db, organiser):
    upload(browser(organiser), "username,display_name,password\nnew1,New One,fresh-password-1\n")
    b = browser()
    assert b.login(db.scalar(select(User).where(User.username == "new1")), "fresh-password-1").headers["location"] == "/"
    assert b.get("/organiser/users").status_code == 403  # imported as a student, so no organiser pages

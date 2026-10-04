"""Create many accounts at once from a CSV file made from the template.

Template columns (the header row is required; the order is free):

    username      required   login name; stored in lower case
    display_name  required   name shown in the interface
    role          optional   student (default) or organiser
    year_group    optional   e.g. UG2
    password      optional   leave empty to have one generated

The import is all or nothing: if anything in the file is wrong, no account is created and every
problem is reported with its row number (row 1 is the header, as in a spreadsheet).
"""

import csv
import io
import secrets
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.enums import Role
from app.errors import AppError
from app.i18n import N_
from app.models import User
from app.services import users

TEMPLATE_COLUMNS = ["username", "display_name", "role", "year_group", "password"]
REQUIRED_COLUMNS = ["username", "display_name"]
MAX_ROWS = 200  # hashing is deliberately slow (~30 ms each), so keep one request short
MAX_BYTES = 256 * 1024
# Generated passwords avoid look-alike characters (0/O, 1/l/I) because people retype them.
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
GENERATED_PASSWORD_LENGTH = 10


@dataclass(frozen=True)
class Problem:
    """Something wrong in the file. `row` is None for problems with the file as a whole."""

    row: int | None
    template: str  # English message with {placeholders}; translated where it is shown
    params: dict = field(default_factory=dict)


class UserImportError(Exception):
    def __init__(self, problems: list[Problem]):
        super().__init__(f"{len(problems)} problem(s) in the file")
        self.problems = problems


@dataclass(frozen=True)
class CreatedAccount:
    username: str
    display_name: str
    role: Role
    year_group: str | None
    password: str | None  # only set when the password was generated; supplied passwords are not repeated
    generated: bool


def generate_password() -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(GENERATED_PASSWORD_LENGTH))


def _cell(value: str) -> str:
    """Make a value safe to open in a spreadsheet: text starting with = + - @ would be run as a formula."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def template_csv() -> str:
    """The empty template: just the header row (with a byte-order mark so Excel reads it as UTF-8)."""
    return "﻿" + ",".join(TEMPLATE_COLUMNS) + "\r\n"


def credentials_csv(accounts: list[CreatedAccount]) -> str:
    """The created accounts with their generated passwords, for the organiser to hand out."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(["username", "display_name", "role", "year_group", "password"])
    for a in accounts:
        writer.writerow([_cell(a.username), _cell(a.display_name), a.role.value, _cell(a.year_group or ""), a.password or ""])
    return "﻿" + out.getvalue()


def _decode(raw: bytes) -> str:
    # Excel on Chinese Windows saves "CSV" as GBK unless "CSV UTF-8" is chosen, so accept both.
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UserImportError([Problem(None, N_("The file could not be read. Save it as CSV (UTF-8) and try again."))])


def _read_rows(raw: bytes) -> tuple[list[tuple[int, dict[str, str]]], list[Problem]]:
    """Return the data rows as (row number, {column: value}) and the problems found in their shape.

    Blank rows are skipped. A row with more values than there are columns is reported instead of being
    cut short, because an unquoted comma in the last column would otherwise silently shorten a value.
    """
    if len(raw) > MAX_BYTES:
        raise UserImportError([Problem(None, N_("The file is too large (the limit is {kb} KB)."), {"kb": MAX_BYTES // 1024})])
    text = _decode(raw)
    first_line = text.lstrip("﻿").split("\n", 1)[0]
    if not first_line.strip():
        raise UserImportError([Problem(None, N_("The file is empty."))])
    # Excel in some regions separates with semicolons or tabs instead of commas.
    delimiter = max(",;\t", key=first_line.count)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = [h.strip().lower().replace(" ", "_") for h in next(reader)]

    problems: list[Problem] = []
    for column in REQUIRED_COLUMNS:
        if column not in header:
            problems.append(Problem(None, N_("The file has no '{column}' column."), {"column": column}))
    for column in dict.fromkeys(header):
        if column not in TEMPLATE_COLUMNS:
            problems.append(Problem(None, N_("Unknown column '{column}'. Use the template's columns."), {"column": column}))
        elif header.count(column) > 1:
            problems.append(Problem(None, N_("Column '{column}' appears more than once."), {"column": column}))
    if problems:
        raise UserImportError(problems)

    rows: list[tuple[int, dict[str, str]]] = []
    shape_problems: list[Problem] = []
    for number, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        if any(c.strip() for c in cells[len(header) :]):
            shape_problems.append(Problem(number, N_("The row has more values than there are columns. Put a value that contains a comma in quotes.")))
            continue
        cells = cells[: len(header)] + [""] * (len(header) - len(cells))
        rows.append((number, dict(zip(header, cells, strict=True))))
    if not rows and not shape_problems:
        raise UserImportError([Problem(None, N_("The file has no accounts to import."))])
    if len(rows) > MAX_ROWS:
        raise UserImportError([Problem(None, N_("The file has more than {max} accounts; import it in smaller parts."), {"max": MAX_ROWS})])
    return rows, shape_problems


def import_users(db: Session, raw: bytes) -> list[CreatedAccount]:
    """Create every account in the file, or none of them (raises UserImportError listing all problems)."""
    rows, problems = _read_rows(raw)

    taken = set(db.scalars(select(User.username)))
    seen: set[str] = set()
    pending: list[tuple[User, str | None]] = []  # (user, generated password)
    for number, row in rows:
        username = users.normalise_username(row.get("username", ""))
        try:
            role_text = row.get("role", "").strip().lower() or Role.student.value
            try:
                role = Role(role_text)
            except ValueError:
                raise AppError(422, "VALIDATION_ERROR", N_("Unknown role '{role}'; use student or organiser."), role=role_text) from None
            supplied = row.get("password", "")
            password = supplied if supplied.strip() else generate_password()
            user = users.build_user(username, row.get("display_name", ""), password, role, row.get("year_group", ""))
        except AppError as exc:
            problems.append(Problem(number, exc.template, exc.params))
            continue
        if user.username in taken:
            problems.append(Problem(number, N_("Username '{username}' is already taken."), {"username": user.username}))
        elif user.username in seen:
            problems.append(Problem(number, N_("Username '{username}' appears more than once in the file."), {"username": user.username}))
        else:
            seen.add(user.username)
            pending.append((user, None if supplied.strip() else password))
    if problems:
        raise UserImportError(sorted(problems, key=lambda p: p.row or 0))

    db.add_all([user for user, _ in pending])
    try:
        db.commit()
    except IntegrityError:  # someone created the same username while this file was being processed
        db.rollback()
        raise UserImportError([Problem(None, N_("An account was created while importing; please try again."))]) from None
    return [
        CreatedAccount(u.username, u.display_name, u.role, u.year_group, generated, generated is not None)
        for u, generated in pending
    ]

"""Interface translations (English and Chinese).

Text is written in English in the code and templates. For another language it is
looked up in that language's catalogue (app/i18n/zh.py), and an entry that is
missing falls back to the English text. A test fails if any marked string lacks
a Chinese entry.

Mark a string for translation with N_() in Python or _() in templates, e.g.
{{ _("Hello {name}", name=user.display_name) }}. Placeholders use str.format syntax.
"""

from datetime import date

from pydantic import ValidationError

from app.i18n.zh import ZH

LANGUAGES = {"en": "English", "zh": "中文"}
DEFAULT_LANGUAGE = "en"
CATALOGUES = {"zh": ZH}

_ZH_WEEKDAYS = "一二三四五六日"


class Msg(str):
    """A string marked as translatable. Only Msg values are translated when passed as parameters,
    so user-entered text that happens to equal a catalogue entry is never altered."""


def N_(text: str) -> Msg:
    """Mark text for translation without translating it yet (the text is translated where it is shown)."""
    return Msg(text)


# Lower-case status words for use inside sentences (pass them as parameters of a marked message).
STATUS_WORD = {
    "draft": N_("draft"),
    "active": N_("active"),
    "closed": N_("closed"),
    "archived": N_("archived"),
    "pending": N_("pending"),
    "approved": N_("approved"),
    "rejected": N_("rejected"),
    "proposed": N_("proposed"),
    "confirmed": N_("confirmed"),
    "completed": N_("completed"),
    "cancelled": N_("cancelled"),
}


def translate(language: str, text: str, **params) -> str:
    if language != DEFAULT_LANGUAGE:
        text = CATALOGUES.get(language, {}).get(text, text)
        params = {k: translate(language, v) if isinstance(v, Msg) else v for k, v in params.items()}
    return text.format(**params) if params else str(text)


def translate_error(language: str, error) -> str:
    """The message of an AppError in the given language (the API always uses the English text)."""
    return translate(language, error.template, **error.params)


def format_date(language: str, value: date) -> str:
    if language == "zh":
        return f"{value.year}年{value.month}月{value.day}日 周{_ZH_WEEKDAYS[value.weekday()]}"
    return value.strftime("%a %d %b %Y")


# Names of form fields and descriptions of validation problems, for form error messages.
FIELD_NAMES = {
    "title": N_("Title"),
    "description": N_("Description"),
    "category": N_("Category"),
    "keywords": N_("Keywords"),
    "rationale": N_("Rationale"),
    "topic_type": N_("Type"),
    "status": N_("Status"),
    "teaching_week": N_("Teaching week"),
    "session_date": N_("Date"),
    "start_time": N_("Start time"),
    "notes": N_("Notes"),
    "topic_id": N_("Topic"),
    "review_note": N_("Note"),
    "semester_name": N_("Semester name"),
    "target_session_count": N_("Total sessions"),
    "target_core_count": N_("Core sessions"),
    "target_optional_count": N_("Optional sessions"),
    "categories": N_("Topic categories"),
    "title_zh": N_("Chinese title"),
    "description_zh": N_("Chinese description"),
}
_PROBLEMS = {
    "missing": N_("is required."),
    "enum": N_("is not a valid choice."),
    "literal_error": N_("is not a valid choice."),
    "int_parsing": N_("must be a whole number."),
    "int_type": N_("must be a whole number."),
    "greater_than_equal": N_("is out of range."),
    "less_than_equal": N_("is out of range."),
    "date_parsing": N_("is not a valid date."),
    "date_from_datetime_parsing": N_("is not a valid date."),
    "date_type": N_("is not a valid date."),
    "time_parsing": N_("is not a valid time."),
    "time_type": N_("is not a valid time."),
}
# Messages raised by the schema validators, matched by their English text.
_VALUE_ERRORS = (
    N_("At most {max} keywords are allowed."),
    N_("Each keyword may have at most {max} characters."),
)


def describe_validation_error(language: str, exc: ValidationError) -> str:
    """One readable sentence about the first problem in a Pydantic validation error."""
    first = exc.errors()[0]
    key = first["loc"][0] if first["loc"] else ""
    field = translate(language, FIELD_NAMES.get(key, N_("Input")))
    kind, ctx = first["type"], first.get("ctx", {})
    if kind == "string_too_short":
        problem = (
            translate(language, N_("must not be empty."))
            if ctx.get("min_length") == 1
            else translate(language, N_("must have at least {n} characters."), n=ctx.get("min_length"))
        )
    elif kind == "string_too_long":
        problem = translate(language, N_("must have at most {n} characters."), n=ctx.get("max_length"))
    elif kind in _PROBLEMS:
        problem = translate(language, _PROBLEMS[kind])
    elif kind == "value_error":
        problem = _translate_value_error(language, str(ctx.get("error", first["msg"])))
    else:
        problem = first["msg"]
    return translate(language, N_("{field}: {problem}"), field=field, problem=problem)


def _translate_value_error(language: str, message: str) -> str:
    for template in _VALUE_ERRORS:
        prefix, _, suffix = template.partition("{max}")
        if message.startswith(prefix) and message.endswith(suffix):
            return translate(language, template, max=message[len(prefix) : len(message) - len(suffix)])
    return message

"""Application-level errors.

Services raise AppError; the API turns it into the JSON error body from SRS 8.11
and the web UI turns it into a flash message or an error page.

`message` may contain {placeholders} filled from `params`. The English text is
what the API returns; the web UI translates the template (see app/i18n).
"""


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, **params):
        self.status_code = status_code
        self.code = code
        self.template = message
        self.params = params
        self.message = message.format(**params) if params else message
        super().__init__(self.message)

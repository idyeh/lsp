"""Application-level errors.

Services raise AppError; the API turns it into the JSON error body from SRS 8.11
and the web UI turns it into a flash message or an error page.
"""


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message

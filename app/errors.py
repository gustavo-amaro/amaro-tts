"""Domain errors. Services know nothing about HTTP; server.py maps them to status codes.

Each error carries a stable `code` (an i18n key the UI translates, see app/static/locales)
plus `params` for interpolation; the message itself is the English fallback.
"""


class AppError(Exception):
    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code = code
        self.params = params


class NotFound(AppError):
    pass


class Conflict(AppError):
    pass


class InvalidInput(AppError):
    pass

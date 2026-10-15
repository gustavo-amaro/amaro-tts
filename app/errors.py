"""Erros de domínio. Os serviços não conhecem HTTP; o server.py traduz para status codes."""


class AppError(Exception):
    pass


class NotFound(AppError):
    pass


class Conflict(AppError):
    pass


class InvalidInput(AppError):
    pass

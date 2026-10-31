class ExchangeError(RuntimeError):
    """An exchange or network error which can be shown without exposing credentials."""


class MEXCError(ExchangeError):
    """A MEXC exchange or network error."""


class CredentialsError(ValueError):
    """An account configuration or decryption error safe to display."""

"""Исключения KKL-слоя."""


class KKLConnectionError(Exception):
    """Базовая ошибка соединения KKL."""


class KKLInitError(KKLConnectionError):
    """Ошибка инициализации шины."""


class KKLTimeoutError(KKLConnectionError):
    """Таймаут при обмене."""


class KKLChecksumError(KKLConnectionError):
    """Неверная контрольная сумма кадра."""

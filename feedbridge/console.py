"""
console.py — вывод в терминал: цвета и форматирование.

Назначение
----------
Собирает в одном месте всё, что относится к оформлению консольного вывода:
ANSI-коды цветов и небольшие помощники форматирования. Используется и
bridge.py (прогресс снапшота, статистика листенера), и db_info.py (таблицы),
чтобы оформление было единым и не дублировалось.

Класс
-----
C                  Набор ANSI-кодов (цвета и стили) как строковые константы.

Функции
-------
fmt_rows(n)        Число строк с разделителями тысяч: 12345 → "12,345".
fmt_mb(n_bytes)    Размер в байтах → строка в мебибайтах ("< 0.01" для крошечных).
fmt_lag(seconds)   Отставание в секундах → "< 1с" / "42с" / "1м 23с".
phase_done(...)    Печатает завершённый этап снапшота: «label… summary  время».
progress(...)      Печатает строку прогресса «label… done/total (pct%)» (с \r).
"""


class C:
    """ANSI-коды цвета и стиля для оформления вывода в терминал."""
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    GRAY   = "\033[90m"
    RED    = "\033[91m"


def fmt_rows(n: int) -> str:
    """Целое число с разделителями тысяч: 12345 → '12,345'."""
    return f"{n:,}"


def fmt_mb(n_bytes: int) -> str:
    """Байты → строка в мебибайтах; '< 0.01' для очень малых значений."""
    mb = n_bytes / 1_048_576
    if mb < 0.01:
        return "< 0.01"
    return f"{mb:,.2f}"


def fmt_lag(seconds: float) -> str:
    """Отставание в секундах → читаемая строка: '< 1с' / '42с' / '1м 23с'."""
    if seconds < 1:
        return "< 1с"
    if seconds < 60:
        return f"{seconds:.0f}с"
    return f"{int(seconds)//60}м {int(seconds)%60:02d}с"


def fmt_duration(elapsed: float) -> str:
    """Длительность в секундах → 'X.X с' (< минуты) или 'Nм M с'."""
    if elapsed < 60:
        return f"{elapsed:.1f} с"
    return f"{int(elapsed)//60}м {int(elapsed)%60} с"


def phase_done(label: str, summary: str, elapsed: float) -> None:
    """Напечатать завершённый этап снапшота: «label… summary  длительность»."""
    print(f"  {C.GRAY}{label}… {C.WHITE}{summary:<32}{C.GRAY}{fmt_duration(elapsed)}{C.RESET}",
          flush=True)


def progress(label: str, done: int, total: int) -> None:
    """Напечатать строку прогресса «label… done/total (pct%)» с возвратом каретки."""
    pct = done * 100 // total if total else 100
    print(f"  {C.GRAY}{label}… {done:,}/{total:,} ({pct}%){C.RESET}", end="\r", flush=True)

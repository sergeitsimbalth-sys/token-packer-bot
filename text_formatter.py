# text_formatter.py — форматирование входного текста под правило "фразы" -> "..."~N
import re
from pathlib import Path
from typing import Tuple


def load_text(path: Path) -> str:
    """Загружает текст из файла в UTF-8."""
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {path}")
    if not path.is_file():
        raise ValueError(f"Указан не файл: {path}")
    return path.read_text(encoding="utf-8").strip()


def transform_item(item: str, n: int) -> str | None:
    """Очищает и преобразует элемент согласно правилам."""
    if not item:
        return None

    s = item.strip()

    # Убираем внешние кавычки
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()

    # Убираем завершающую пунктуацию (. ! ? ; : …)
    s = re.sub(r"""[\.!\?;:…]+$""", "", s, flags=re.VERBOSE)

    # Заменяем дефисы, тире и подчеркивания на пробел
    s = re.sub(r"[-–—_]", " ", s)

    # Схлопываем пробелы
    s = re.sub(r"\s+", " ", s).strip()

    if not s:
        return None

    # Проверяем количество слов
    if " " in s:  # минимум два слова
        return f"\"{s}\"~{n}"
    else:
        return s


def process_text(text: str, n: int) -> Tuple[str, int, int, int]:
    """
    Обрабатывает весь текст и возвращает (result, total, phrases, singles).
    Разделитель элементов — запятая.
    """
    items = [x.strip() for x in text.split(",")]

    result_items = []
    phrases = 0
    singles = 0

    for item in items:
        transformed = transform_item(item, n)
        if transformed is None:
            continue
        result_items.append(transformed)
        if transformed.startswith('"'):
            phrases += 1
        else:
            singles += 1

    result = ", ".join(result_items)
    return result, len(result_items), phrases, singles


def save_text(path: Path, text: str) -> Path:
    """Сохраняет результат рядом с исходным файлом с суффиксом _formatted.txt."""
    out_path = path.with_name(path.stem + "_formatted.txt")
    out_path.write_text(text, encoding="utf-8")
    return out_path

# bot.py — Telegram-бот: упаковка токенов + форматирование текста (/format)
import os
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from token_packer import pack, normalize_tokens
from text_formatter import process_text  # новый модуль

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния для "упаковщика"
LEFT, RIGHT, MINLEN, MAXLEN, SEPARATOR = range(5)
# Состояния для форматтера
FMT_TEXT, FMT_N = range(5, 7)


def _auto_wrap_separator(sep: str) -> str:
    s = (sep or "").strip()
    if not s:
        return ") * ("
    if "(" not in s and ")" not in s:
        return f"){s}("
    return s


# ======== УПАКОВЩИК (старый диалог) ========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт/перезапуск упаковщика."""
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Давай соберём токены.\nВведи ЛЕВУЮ часть (фиксированный список слов):"
    )
    return LEFT


async def left_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["left"] = normalize_tokens([update.message.text])
    if not context.user_data["left"]:
        await update.message.reply_text("Левая часть пустая. Введите хотя бы одно слово:")
        return LEFT
    await update.message.reply_text("Отлично! Теперь введи ПРАВУЮ часть (плавающий список слов):")
    return RIGHT


async def right_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["right"] = normalize_tokens([update.message.text])
    if not context.user_data["right"]:
        await update.message.reply_text("Правая часть пустая. Введите хотя бы одно слово:")
        return RIGHT
    await update.message.reply_text("Введи минимальную длину конструкции (например 480):")
    return MINLEN


async def minlen_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["min_len"] = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Ошибка! Введи число (например 480):")
        return MINLEN
    await update.message.reply_text("Теперь введи максимальную длину конструкции (например 512):")
    return MAXLEN


async def maxlen_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["max_len"] = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Ошибка! Введи число (например 512):")
        return MAXLEN
    if context.user_data["min_len"] > context.user_data["max_len"]:
        await update.message.reply_text("min_len не может быть больше max_len. Введите max_len ещё раз:")
        return MAXLEN
    await update.message.reply_text(
        "Теперь введи разделитель (например ')*(' или ')/1(' — скобочки можно не писать):"
    )
    return SEPARATOR


async def separator_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    separator = _auto_wrap_separator(update.message.text)
    ud["separator"] = separator

    try:
        results = pack(ud["left"], ud["right"], ud["min_len"], ud["max_len"], separator)
        out_text = ", ".join(results)

        if len(out_text) > 4000:
            path = f"result_{update.effective_user.id}.txt"
            with open(path, "w", encoding="utf-8") as f:
                f.write(out_text)
            with open(path, "rb") as f:
                await update.message.reply_document(document=f)
            os.remove(path)
        else:
            await update.message.reply_text(out_text)

        lengths = [f"#{i+1}: {len(c)} символов" for i, c in enumerate(results)]
        await update.message.reply_text("\n".join(lengths))

    except Exception as e:
        logger.exception("Ошибка при упаковке")
        await update.message.reply_text(f"Ошибка: {e}")

    return ConversationHandler.END


# ======== ФОРМАТТЕР ТЕКСТА (/format) ========

async def format_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт форматтера: ждём .txt или просто текст."""
    # не очищаем весь user_data, чтобы не мешать параллельным сессиям упаковщика
    context.user_data.pop("fmt_text", None)
    await update.message.reply_text(
        "Режим форматирования.\nПришлите .txt файл ИЛИ вставьте текст сообщением (через запятую):"
    )
    return FMT_TEXT


async def fmt_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимаем .txt или текст, сохраняем в user_data['fmt_text'] и спрашиваем N."""
    text: str | None = None

    # Если прислали документ .txt
    if update.message.document and update.message.document.mime_type == "text/plain":
        doc = update.message.document
        # Ограничение размера на всякий случай (например, 5 МБ)
        if doc.file_size and doc.file_size > 5 * 1024 * 1024:
            await update.message.reply_text("Файл слишком большой (>5 МБ). Пришлите меньший файл.")
            return FMT_TEXT
        tgfile = await doc.get_file()
        tmp_path = Path(f"upload_{update.effective_user.id}.txt")
        await tgfile.download_to_drive(custom_path=str(tmp_path))
        try:
            text = tmp_path.read_text(encoding="utf-8")
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    # Если прислали просто текст
    elif update.message.text:
        text = update.message.text

    if not text or not text.strip():
        await update.message.reply_text("Пустой ввод. Пришлите .txt или вставьте текст сообщением:")
        return FMT_TEXT

    context.user_data["fmt_text"] = text.strip()
    await update.message.reply_text("Введите целое число N для тильды (по умолчанию 0):")
    return FMT_N


async def fmt_n_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Парсим N, форматируем, отправляем файл и статистику."""
    n_str = (update.message.text or "").strip()
    try:
        n = int(n_str) if n_str else 0
    except ValueError:
        await update.message.reply_text("Ошибка! Введите целое число N (например 0, 1, 2):")
        return FMT_N

    text = context.user_data.get("fmt_text", "")
    try:
        result, t

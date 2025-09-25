# bot.py — Telegram-бот: упаковка токенов + форматирование текста
import os
import logging
from pathlib import Path

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

from token_packer import pack, normalize_tokens
from text_formatter import process_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния диалога "упаковщика"
LEFT, RIGHT, MINLEN, MAXLEN, SEPARATOR = range(5)
# Состояния диалога "форматтера"
FMT_TEXT, FMT_N = range(5, 7)


def _auto_wrap_separator(sep: str) -> str:
    """Если пользователь не добавил скобки — обернём автоматически."""
    s = (sep or "").strip()
    if not s:
        return ") * ("
    if "(" not in s and ")" not in s:
        return f"){s}("
    return s


# ======== УПАКОВЩИК (группировка) ========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт/перезапуск группировки + краткая справка."""
    context.user_data.clear()
    help_text = (
        "🧩 Что умеет бот:\n"
        "• Группировка (упаковка токенов) — команда /start\n"
        "  — Введите ЛЕВУЮ часть (фиксированный список слов), затем ПРАВУЮ (плавающий список),\n"
        "    затем min_len, max_len и разделитель (например ')*('). Бот вернёт конструкции и их длины.\n\n"
        "• Форматирование текста — команда /format\n"
        "  — Пришлите .txt или вставьте текст через запятую, затем число N.\n"
        '    Фразы (2+ слова) будут преобразованы в "фразу"~N, дефисы/подчёркивания очищаются.\n\n'
        "Сервисные: /reset — сброс и перезапуск, /cancel — отмена, /help — подсказка.\n\n"
        "Ок! Теперь начнём группировку.\n"
        "Введи ЛЕВУЮ часть (фиксированный список слов):"
    )
    await update.message.reply_text(help_text)
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

        if len(out_text) > 4000:  # запас до 4096 лимита сообщения
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


# ======== ФОРМАТТЕР (/format) ========

async def format_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт форматтера: ждём .txt или просто текст."""
    context.user_data.pop("fmt_text", None)
    await update.message.reply_text(
        "Режим форматирования.\nПришлите .txt файл ИЛИ вставьте текст сообщением (через запятую):"
    )
    return FMT_TEXT


async def fmt_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принимаем .txt или текст, сохраняем и спрашиваем число N."""
    text: str | None = None

    if update.message.document and update.message.document.mime_type == "text/plain":
        doc = update.message.document
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
        result, total, phrases, singles = process_text(text, n)

        out_path = Path(f"formatted_{update.effective_user.id}.txt")
        out_path.write_text(result, encoding="utf-8")
        try:
            with open(out_path, "rb") as f:
                await update.message.reply_document(document=f, filename=out_path.name)
        finally:
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass

        preview = result[:200]
        await update.message.reply_text(
            f"Готово ✅\nВсего элементов: {total}\nФраз: {phrases}\nОдиночных слов: {singles}\nПредпросмотр: {preview}"
        )

    except Exception as e:
        logger.exception("Ошибка при форматировании")
        await update.message.reply_text(f"Ошибка: {e}")

    context.user_data.pop("fmt_text", None)
    return ConversationHandler.END


# ======== Сервисные и справка ========

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("⛔ Операция отменена.")
    return ConversationHandler.END


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("🔁 Сбросил текущую сессию. Начнём заново.\nВведи ЛЕВУЮ часть:")
    return LEFT


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🧩 Справка:\n"
        "• Группировка — /start\n"
        "  1) ЛЕВАЯ часть → 2) ПРАВАЯ часть → 3) min_len → 4) max_len → 5) разделитель ('*(' и т.п.)\n"
        "• Форматирование — /format\n"
        "  — Пришлите .txt или текст через запятую, затем N. Фразы → \"...\"~N\n"
        "• Сервисные: /reset — сброс и перезапуск, /cancel — отмена, /help — подсказка\n"
    )
    await update.message.reply_text(help_text)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception", exc_info=context.error)


async def post_init(app: Application):
    """Меню команд в Telegram-клиенте."""
    await app.bot.set_my_commands([
        BotCommand("start", "Группировка (упаковка токенов)"),
        BotCommand("format", "Форматирование текста в \"...\"~N"),
        BotCommand("reset", "Сброс диалога/перезапуск"),
        BotCommand("cancel", "Отмена текущей операции"),
        BotCommand("help", "Показать краткую справку"),
    ])


def build_app() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")

    app = Application.builder().token(token).post_init(post_init).build()

    # Диалог "форматтера"
    conv_fmt = ConversationHandler(
        entry_points=[CommandHandler("format", format_start)],
        states={
            FMT_TEXT: [
                MessageHandler(filters.Document.FileExtension("txt"), fmt_text_input),
                MessageHandler(filters.TEXT & ~filters.COMMAND, fmt_text_input),
            ],
            FMT_N: [MessageHandler(filters.TEXT & ~filters.COMMAND, fmt_n_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                   CommandHandler("reset", reset),
                   CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=600,
        name="conv_fmt",
        persistent=False,
    )

    # Диалог "упаковщика"
    conv_pack = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LEFT: [MessageHandler(filters.TEXT & ~filters.COMMAND, left_input)],
            RIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, right_input)],
            MINLEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, minlen_input)],
            MAXLEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, maxlen_input)],
            SEPARATOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, separator_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel),
                   CommandHandler("reset", reset),
                   CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=600,
        name="conv_pack",
        persistent=False,
    )

    # Глобальные команды
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reset", start))
    app.add_handler(conv_fmt)
    app.add_handler(conv_pack)

    app.add_error_handler(on_error)
    return app


def main():
    app = build_app()
    # Webhook на Render (или любой PaaS) / Polling локально
    base = os.getenv("WEBHOOK_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
    port = int(os.getenv("PORT", "10000"))
    path = f"/webhook/{os.getenv('WEBHOOK_PATH', 'tg')}"
    if base:
        app.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=path,
            webhook_url=base.rstrip("/") + path,
        )
    else:
        app.run_polling()


if __name__ == "__main__":
    main()

# bot.py — Telegram-бот: меню /start, группировка /gpupirovka, форматирование /format,
# перезапуск /reset, НОВОЕ: нормализация «тональности» /tonalnost
import os
import logging
from pathlib import Path

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
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
from tonalnost_formatter import normalize_message  # <-- НОВОЕ

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния группировки
LEFT, RIGHT, MINLEN, MAXLEN, SEPARATOR = range(5)
# Состояния форматтера
FMT_TEXT, FMT_N = range(5, 7)
# Состояния тональности
TON_TEXT = 7  # один шаг ввода текста

def _kb_main():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("/gpupirovka"), KeyboardButton("/format")],
            [KeyboardButton("/tonalnost"), KeyboardButton("/reset")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )

def _auto_wrap_separator(sep: str) -> str:
    s = (sep or "").strip()
    if not s:
        return ") * ("
    if "(" not in s and ")" not in s:
        return f"){s}("
    return s

# ========================== /start ==========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    txt = (
        "👋 Привет! Доступны режимы:\n\n"
        "• /gpupirovka — ГРУППИРОВКА:  Собирает длинные списки ключей в пары скобок так, чтобы каждая пара укладывалась в лимит ~512 символов.\n"
        "• /format — ФОРМАТИРОВАНИЕ: Форматирует список слов под формат поискового запроса.\n"
        "• /tonalnost — ТОНАЛЬНОСТЬ: Приводит слова к правильному формату для объекта тональности. —\n"
        "В любой момент нажмите /reset, чтобы вернуться в это меню."
    )
    await update.message.reply_text(txt, reply_markup=_kb_main())
    return ConversationHandler.END

# ========================== ГРУППИРОВКА ==========================
async def gpupirovka_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text(
        "Режим ГРУППИРОВКИ.\nВведи ЛЕВУЮ часть (фиксированный список слов):",
        reply_markup=_kb_main(),
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

# ========================== ФОРМАТИРОВАНИЕ (/format) ==========================
async def format_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("fmt_text", None)
    await update.message.reply_text(
        "Режим ФОРМАТИРОВАНИЯ.\nПришлите .txt файл ИЛИ вставьте текст сообщением (через запятую):",
        reply_markup=_kb_main(),
    )
    return FMT_TEXT

async def fmt_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# ========================== ТОНАЛЬНОСТЬ (/tonalnost) ==========================
async def tonalnost_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Режим НОРМАЛИЗАЦИИ.\n"
        "Вставьте текст (через запятую). Пример:\n"
        "пожарная часть, пожарные, сотрудники, мчс, \"оперативная cитуация\"~0, администрация",
        reply_markup=_kb_main(),
    )
    return TON_TEXT

async def tonalnost_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    src = (update.message.text or "").strip()
    if not src:
        await update.message.reply_text("Пустой ввод. Вставьте текст через запятую.")
        return TON_TEXT
    try:
        result, notes = normalize_message(src)
        # Результат — если длинный, отдаём файлом
        if len(result) > 4000:
            out_path = Path(f"tonalnost_{update.effective_user.id}.txt")
            out_path.write_text(result, encoding="utf-8")
            try:
                with open(out_path, "rb") as f:
                    await update.message.reply_document(document=f, filename=out_path.name)
            finally:
                try:
                    out_path.unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            await update.message.reply_text(result)

        # Пояснения (ограничим до ~3500 символов в сообщении)
        if notes:
            joined = "• " + "\n• ".join(notes)
            if len(joined) > 3500:
                rep_path = Path(f"tonalnost_report_{update.effective_user.id}.txt")
                rep_path.write_text(joined, encoding="utf-8")
                try:
                    with open(rep_path, "rb") as f:
                        await update.message.reply_document(document=f, filename=rep_path.name)
                finally:
                    try:
                        rep_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            else:
                await update.message.reply_text("Пояснения:\n" + joined)
    except Exception as e:
        logger.exception("Ошибка в /tonalnost")
        await update.message.reply_text(f"Ошибка: {e}")
    return ConversationHandler.END

# ========================== ОБЩЕЕ ==========================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("⛔ Операция отменена.")
    return ConversationHandler.END

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await start(update, context)
    return ConversationHandler.END

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception", exc_info=context.error)

def build_app() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    app = Application.builder().token(token).build()

    # Группировка
    conv_pack = ConversationHandler(
        entry_points=[CommandHandler("gpupirovka", gpupirovka_start)],
        states={
            LEFT: [MessageHandler(filters.TEXT & ~filters.COMMAND, left_input)],
            RIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, right_input)],
            MINLEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, minlen_input)],
            MAXLEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, maxlen_input)],
            SEPARATOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, separator_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("reset", reset), CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=600,
        name="conv_pack",
        persistent=False,
    )

    # Форматирование
    conv_fmt = ConversationHandler(
        entry_points=[CommandHandler("format", format_start)],
        states={
            FMT_TEXT: [
                MessageHandler(filters.Document.FileExtension("txt"), fmt_text_input),
                MessageHandler(filters.TEXT & ~filters.COMMAND, fmt_text_input),
            ],
            FMT_N: [MessageHandler(filters.TEXT & ~filters.COMMAND, fmt_n_input)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("reset", reset), CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=600,
        name="conv_fmt",
        persistent=False,
    )

    # Тональность
    conv_ton = ConversationHandler(
        entry_points=[CommandHandler("tonalnost", tonalnost_start)],
        states={TON_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, tonalnost_process)]},
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("reset", reset), CommandHandler("start", start)],
        allow_reentry=True,
        conversation_timeout=600,
        name="conv_ton",
        persistent=False,
    )

    # Глобальные команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(conv_ton)
    app.add_handler(conv_fmt)
    app.add_handler(conv_pack)

    app.add_error_handler(on_error)
    return app

def main():
    app = build_app()
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

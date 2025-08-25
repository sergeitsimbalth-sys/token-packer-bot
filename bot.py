# bot.py — Telegram-бот с быстрым перезапуском диалога и webhook/polling-режимом
import os
import logging
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния диалога
LEFT, RIGHT, MINLEN, MAXLEN, SEPARATOR = range(5)


def _auto_wrap_separator(sep: str) -> str:
    """Если пользователь не добавил скобки — обернём автоматически."""
    s = (sep or "").strip()
    if not s:
        return ") * ("
    if "(" not in s and ")" not in s:
        return f"){s}("
    return s


# ======== Хэндлеры состояний ========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Старт или перезапуск диалога."""
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
        "Теперь введи разделитель (например ')*(' или ')/1(' — обязательно пишем скобочки):"
    )
    return SEPARATOR


async def separator_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    separator = _auto_wrap_separator(update.message.text)
    ud["separator"] = separator

    try:
        results = pack(ud["left"], ud["right"], ud["min_len"], ud["max_len"], separator)
        out_text = ", ".join(results)

        if len(out_text) > 4000:  # запас до 4096
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


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("⛔ Операция отменена.")
    return ConversationHandler.END


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сброс из любой точки: очищаем состояние и возвращаемся на шаг LEFT."""
    context.user_data.clear()
    await update.message.reply_text("🔁 Сбросил текущую сессию. Начнём заново.\nВведи ЛЕВУЮ часть:")
    return LEFT


# Глобальный обработчик ошибок, чтобы видеть стек-трейс в логах Render
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception", exc_info=context.error)


def build_app() -> Application:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LEFT: [MessageHandler(filters.TEXT & ~filters.COMMAND, left_input)],
            RIGHT: [MessageHandler(filters.TEXT & ~filters.COMMAND, right_input)],
            MINLEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, minlen_input)],
            MAXLEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, maxlen_input)],
            SEPARATOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, separator_input)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("reset", reset),
            CommandHandler("start", start),
        ],
        allow_reentry=True,
        conversation_timeout=600,  # автозавершение диалога через 10 минут простоя
    )

    # Добавим /reset глобально, чтобы он работал и вне диалога
    app.add_handler(CommandHandler("reset", start))
    app.add_handler(conv)
    app.add_error_handler(on_error)
    return app


def main():
    app = build_app()
    # На Render есть RENDER_EXTERNAL_URL и PORT — используем webhook.
    # Локально/без URL — fallback на polling.
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

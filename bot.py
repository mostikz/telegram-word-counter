import re
import sqlite3
from datetime import datetime, time, timezone, timedelta

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

# ВСТАВЬ СЮДА НОВЫЙ ТОКЕН, КОТОРЫЙ ТЫ ПОЛУЧИЛ
# ПОСЛЕ ОТЗЫВА СТАРОГО ТОКЕНА В BOTFATHER.
TOKEN = "8650768988:AAHvpErqCoyiwudLKouesuIWKcbomEn5J0w"

# Москва = UTC+3
MOSCOW = timezone(timedelta(hours=3))

DATABASE = "stats.db"


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_db():
    return sqlite3.connect(DATABASE)


def init_database():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            date TEXT NOT NULL,
            messages INTEGER DEFAULT 0,
            words INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id, date)
        )
    """)

    db.commit()
    db.close()


# ============================================================
# МОСКОВСКОЕ ВРЕМЯ
# ============================================================

def moscow_now():
    return datetime.now(MOSCOW)


# ============================================================
# ПОДСЧЁТ СЛОВ
# ============================================================

def count_words(text):
    if not text:
        return 0

    words = re.findall(
        r"[^\W_]+(?:[-'][^\W_]+)*",
        text,
        flags=re.UNICODE
    )

    return len(words)


# ============================================================
# СОХРАНЕНИЕ СООБЩЕНИЯ
# ============================================================

def save_message(chat_id, user_id, user_name, words):
    date = moscow_now().strftime("%Y-%m-%d")

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        INSERT INTO daily_stats (
            chat_id,
            user_id,
            user_name,
            date,
            messages,
            words
        )
        VALUES (?, ?, ?, ?, 1, ?)

        ON CONFLICT(chat_id, user_id, date)
        DO UPDATE SET
            user_name = excluded.user_name,
            messages = messages + 1,
            words = words + excluded.words
    """, (
        chat_id,
        user_id,
        user_name,
        date,
        words,
    ))

    db.commit()
    db.close()


# ============================================================
# ПОЛУЧЕНИЕ СТАТИСТИКИ
# ============================================================

def get_today_stats(chat_id):
    date = moscow_now().strftime("%Y-%m-%d")

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT
            user_id,
            user_name,
            messages,
            words
        FROM daily_stats
        WHERE chat_id = ?
          AND date = ?
        ORDER BY words DESC
    """, (chat_id, date))

    rows = cursor.fetchall()

    db.close()

    return rows


# ============================================================
# ФОРМИРОВАНИЕ СТАТИСТИКИ
# ============================================================

def make_stats_text(chat_id):
    rows = get_today_stats(chat_id)

    date = moscow_now().strftime("%d.%m.%Y")

    result = f"📅 Напиздели за сегодня\n\n{date}\n\n"

    if not rows:
        return result + "Пока никто ничего не напиздел 😄"

    total_words = 0
    total_messages = 0

    for position, row in enumerate(rows, start=1):
        user_id, user_name, messages, words = row

        total_words += words
        total_messages += messages

        if position == 1:
            prefix = "🥇"
        elif position == 2:
            prefix = "🥈"
        elif position == 3:
            prefix = "🥉"
        else:
            prefix = f"{position}."

        result += (
            f"{prefix} {user_name} — "
            f"{words:,} слов\n"
        )

    result += (
        f"\n💬 Всего сообщений: {total_messages:,}"
        f"\n📝 Всего слов: {total_words:,}"
    )

    return result


# ============================================================
# ОБРАБОТКА СООБЩЕНИЙ
# ============================================================

async def count_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.message

    if not message:
        return

    # Не считаем сообщения ботов
    if message.from_user and message.from_user.is_bot:
        return

    # Работаем только в группах
    if message.chat.type not in ("group", "supergroup"):
        return

    user = message.from_user

    if not user:
        return

    text = message.text or message.caption or ""

    words = count_words(text)

    save_message(
        chat_id=message.chat.id,
        user_id=user.id,
        user_name=user.full_name,
        words=words,
    )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "Привет! 🤖\n\n"
        "Я считаю, кто сколько напиздел в чате.\n\n"
        "/stats — статистика за сегодня"
    )


# ============================================================
# /STATS
# ============================================================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text(
            "Эту команду нужно использовать в группе."
        )
        return

    text = make_stats_text(update.effective_chat.id)

    await update.message.reply_text(text)


# ============================================================
# АВТОМАТИЧЕСКИЙ ОТЧЁТ В 20:00
# ============================================================

async def daily_report(
    context: ContextTypes.DEFAULT_TYPE
):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT DISTINCT chat_id
        FROM daily_stats
    """)

    chats = cursor.fetchall()

    db.close()

    for (chat_id,) in chats:
        try:
            text = make_stats_text(chat_id)

            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
            )

            print(
                f"Отчёт отправлен в чат {chat_id}"
            )

        except Exception as error:
            print(
                f"Ошибка отправки в {chat_id}: {error}"
            )


# ============================================================
# ЗАПУСК
# ============================================================

def main():

    init_database()

    app = (
        Application
        .builder()
        .token(TOKEN)
        .build()
    )

    # Команды
    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("stats", stats)
    )

    # Все текстовые сообщения и подписи к медиа
    app.add_handler(
        MessageHandler(
            filters.TEXT | filters.CAPTION,
            count_message
        )
    )

    # Каждый день в 20:00 по Москве
    app.job_queue.run_daily(
        daily_report,
        time=time(
            hour=20,
            minute=0,
            tzinfo=MOSCOW
        ),
    )

    print("================================")
    print("Бот запущен!")
    print("Часовой пояс: Москва (UTC+3)")
    print("Ежедневный отчёт: 20:00")
    print("Команда: /stats")
    print("================================")

    app.run_polling()


if __name__ == "__main__":
    main()


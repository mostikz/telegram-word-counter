import os
import sqlite3
from datetime import datetime, time, timezone, timedelta, date

from telegram import (
    Update,
    BotCommand,
)
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

# НЕ вставляй токен прямо в код.
# Linux/macOS:
# export BOT_TOKEN="твой_новый_токен"
#
# Windows PowerShell:
# $env:BOT_TOKEN="твой_новый_токен"

TOKEN = os.getenv("BOT_TOKEN", "PASTE_NEW_TOKEN_HERE")

# Москва = UTC+3
MOSCOW = timezone(timedelta(hours=3))

DATABASE = "stats.db"


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_db():
    db = sqlite3.connect(
        DATABASE,
        timeout=30
    )

    # Позволяет базе лучше работать при одновременном чтении/записи
    db.execute("PRAGMA journal_mode=WAL")

    return db


def init_database():
    db = get_db()
    cursor = db.cursor()

    # --------------------------------------------------------
    # Основная статистика по дням
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            user_name TEXT NOT NULL,
            date TEXT NOT NULL,

            messages INTEGER DEFAULT 0,
            words INTEGER DEFAULT 0,
            characters INTEGER DEFAULT 0,

            longest_message_words INTEGER DEFAULT 0,
            longest_message_chars INTEGER DEFAULT 0,

            PRIMARY KEY (
                chat_id,
                user_id,
                date
            )
        )
    """)

    # --------------------------------------------------------
    # Статистика по часам
    # --------------------------------------------------------

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS hourly_stats (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,

            messages INTEGER DEFAULT 0,
            words INTEGER DEFAULT 0,

            PRIMARY KEY (
                chat_id,
                user_id,
                date,
                hour
            )
        )
    """)

    # --------------------------------------------------------
    # МИГРАЦИЯ СТАРОЙ БАЗЫ
    #
    # Если у тебя уже была старая stats.db,
    # эти поля будут добавлены автоматически.
    # --------------------------------------------------------

    cursor.execute("""
        PRAGMA table_info(daily_stats)
    """)

    columns = {
        row[1]
        for row in cursor.fetchall()
    }

    if "characters" not in columns:
        cursor.execute("""
            ALTER TABLE daily_stats
            ADD COLUMN characters INTEGER DEFAULT 0
        """)

    if "longest_message_words" not in columns:
        cursor.execute("""
            ALTER TABLE daily_stats
            ADD COLUMN longest_message_words INTEGER DEFAULT 0
        """)

    if "longest_message_chars" not in columns:
        cursor.execute("""
            ALTER TABLE daily_stats
            ADD COLUMN longest_message_chars INTEGER DEFAULT 0
        """)

    # Индексы ускоряют статистику за периоды

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_daily_chat_date
        ON daily_stats(chat_id, date)
    """)

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_hourly_chat_date
        ON hourly_stats(chat_id, date)
    """)

    db.commit()
    db.close()


# ============================================================
# ВРЕМЯ
# ============================================================

def moscow_now():
    return datetime.now(MOSCOW)


def today_str():
    return moscow_now().strftime("%Y-%m-%d")


def date_str(value):
    return value.strftime("%Y-%m-%d")


def pretty_date(value):
    return value.strftime("%d.%m.%Y")


def last_n_days(n):
    """
    Возвращает период последних n дней,
    включая сегодняшний.
    """

    today = moscow_now().date()

    start = today - timedelta(
        days=n - 1
    )

    return start, today


def current_month_range():
    """
    Первый день текущего месяца
    и сегодняшний день.
    """

    today = moscow_now().date()

    start = today.replace(day=1)

    return start, today


# ============================================================
# ПОДСЧЁТ СЛОВ
# ============================================================



# ============================================================
# СОХРАНЕНИЕ СООБЩЕНИЯ
# ============================================================

def save_message(
    chat_id,
    user_id,
    user_name,
    hour
):
    """Сохраняет только одно отправленное сообщение."""
    today = today_str()

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        INSERT INTO daily_stats (
            chat_id,
            user_id,
            user_name,
            date,
            messages,
            words,
            characters,
            longest_message_words,
            longest_message_chars
        )
        VALUES (
            ?, ?, ?, ?,
            1,
            0, 0,
            0, 0
        )
        ON CONFLICT(
            chat_id,
            user_id,
            date
        )
        DO UPDATE SET
            user_name = excluded.user_name,
            messages = messages + 1
    """, (
        chat_id,
        user_id,
        user_name,
        today,
    ))

    cursor.execute("""
        INSERT INTO hourly_stats (
            chat_id,
            user_id,
            date,
            hour,
            messages,
            words
        )
        VALUES (
            ?, ?, ?, ?,
            1,
            0
        )
        ON CONFLICT(
            chat_id,
            user_id,
            date,
            hour
        )
        DO UPDATE SET
            messages = messages + 1
    """, (
        chat_id,
        user_id,
        today,
        hour,
    ))

    db.commit()
    db.close()




# ============================================================
# ПОЛУЧЕНИЕ СТАТИСТИКИ
# ============================================================

def get_stats(
    chat_id,
    start_date,
    end_date
):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT
            user_id,
            MAX(user_name),
            SUM(messages)
        FROM daily_stats
        WHERE chat_id = ?
          AND date BETWEEN ? AND ?
        GROUP BY user_id
        ORDER BY messages DESC, user_id ASC
    """, (
        chat_id,
        date_str(start_date),
        date_str(end_date)
    ))

    rows = cursor.fetchall()
    db.close()

    return [
        (
            row[0],
            row[1],
            row[2],
            0,
            0,
            0,
            0,
        )
        for row in rows
    ]


def get_user_stats(
    chat_id,
    user_id,
    start_date,
    end_date
):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT COALESCE(SUM(messages), 0)
        FROM daily_stats
        WHERE chat_id = ?
          AND user_id = ?
          AND date BETWEEN ? AND ?
    """, (
        chat_id,
        user_id,
        date_str(start_date),
        date_str(end_date)
    ))

    messages = cursor.fetchone()[0]
    db.close()

    return (
        messages,
        0,
        0,
        0,
        0,
    )


def get_rank(
    chat_id,
    user_id,
    start_date,
    end_date
):
    rows = get_stats(
        chat_id,
        start_date,
        end_date
    )

    for position, row in enumerate(rows, start=1):
        if row[0] == user_id:
            return position

    return None


def get_group_totals(
    chat_id,
    start_date,
    end_date
):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT
            COALESCE(SUM(messages), 0),
            COUNT(DISTINCT user_id)
        FROM daily_stats
        WHERE chat_id = ?
          AND date BETWEEN ? AND ?
    """, (
        chat_id,
        date_str(start_date),
        date_str(end_date)
    ))

    messages, users = cursor.fetchone()
    db.close()

    return (
        messages,
        0,
        users,
    )




# ============================================================
# АКТИВНОСТЬ ПО ДНЯМ
# ============================================================


# ============================================================
# АКТИВНОСТЬ ПО ДНЯМ
# ============================================================

def get_daily_group_activity(
    chat_id,
    start_date,
    end_date
):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT
            date,
            SUM(messages)
        FROM daily_stats
        WHERE chat_id = ?
          AND date BETWEEN ? AND ?
        GROUP BY date
        ORDER BY date
    """, (
        chat_id,
        date_str(start_date),
        date_str(end_date)
    ))

    rows = cursor.fetchall()
    db.close()

    return [
        (row[0], row[1], 0)
        for row in rows
    ]


def get_hourly_activity(
    chat_id,
    start_date,
    end_date
):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT
            hour,
            SUM(messages)
        FROM hourly_stats
        WHERE chat_id = ?
          AND date BETWEEN ? AND ?
        GROUP BY hour
        ORDER BY hour
    """, (
        chat_id,
        date_str(start_date),
        date_str(end_date)
    ))

    rows = cursor.fetchall()
    db.close()

    return [
        (row[0], row[1], 0)
        for row in rows
    ]




# ============================================================
# НОЧНЫЕ СООБЩЕНИЯ
# ============================================================

def get_night_messages(
    chat_id,
    user_id
):

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT
            COALESCE(
                SUM(messages),
                0
            )

        FROM hourly_stats

        WHERE chat_id = ?
          AND user_id = ?

          AND hour >= 0
          AND hour < 5

    """, (
        chat_id,
        user_id
    ))

    value = cursor.fetchone()[0]

    db.close()

    return value


# ============================================================
# РЕКОРДЫ
# ============================================================

def get_best_records(chat_id):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT
            user_name,
            messages,
            date
        FROM daily_stats
        WHERE chat_id = ?
        ORDER BY messages DESC, date ASC
        LIMIT 1
    """, (chat_id,))

    best_messages = cursor.fetchone()
    db.close()

    return (
        None,
        best_messages,
        None,
    )




# ============================================================
# СЕРИИ АКТИВНОСТИ
# ============================================================

def get_user_dates(
    chat_id,
    user_id
):

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT DISTINCT date

        FROM daily_stats

        WHERE chat_id = ?
          AND user_id = ?

        ORDER BY date DESC

    """, (
        chat_id,
        user_id
    ))

    rows = [
        date.fromisoformat(row[0])
        for row in cursor.fetchall()
    ]

    db.close()

    return rows


def get_streaks(
    chat_id,
    user_id
):

    dates = get_user_dates(
        chat_id,
        user_id
    )

    if not dates:
        return 0, 0

    date_set = set(dates)

    today = moscow_now().date()

    # --------------------------------------------------------
    # Текущая серия
    # --------------------------------------------------------

    current = 0

    cursor_date = today

    while cursor_date in date_set:

        current += 1

        cursor_date -= timedelta(
            days=1
        )

    # --------------------------------------------------------
    # Лучшая серия
    # --------------------------------------------------------

    best = 0

    for d in dates:

        if (
            d - timedelta(days=1)
            not in date_set
        ):

            length = 1

            next_day = (
                d + timedelta(days=1)
            )

            while next_day in date_set:

                length += 1

                next_day += timedelta(
                    days=1
                )

            best = max(
                best,
                length
            )

    return current, best


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def medal(position):

    return {
        1: "🥇",
        2: "🥈",
        3: "🥉",
    }.get(
        position,
        f"{position}."
    )


def format_number(value):

    return f"{int(value):,}".replace(
        ",",
        " "
    )


def format_period(
    start_date,
    end_date
):

    return (
        f"{pretty_date(start_date)}"
        f" — "
        f"{pretty_date(end_date)}"
    )


def bar(
    value,
    max_value,
    width=15
):

    if max_value <= 0:
        return "░" * width

    filled = max(
        1,
        round(
            value / max_value * width
        )
    ) if value else 0

    return (
        "█" * filled
        +
        "░" * (
            width - filled
        )
    )


def stats_lines(
    rows,
    show_messages=False
):
    lines = []

    for position, row in enumerate(
        rows,
        start=1
    ):
        lines.append(
            f"{medal(position)} "
            f"{row[1]} — "
            f"{format_number(row[2])} сообщений"
        )

    return lines




def group_only(update):

    return (
        update.effective_chat
        and
        update.effective_chat.type
        in ("group", "supergroup")
    )


# ============================================================
# СТАТИСТИКА СЕГОДНЯ
# ============================================================

def make_today_stats_text(chat_id):
    today = moscow_now().date()

    rows = get_stats(
        chat_id,
        today,
        today
    )

    result = (
        f"📅 Сообщения за сегодня\n\n"
        f"{pretty_date(today)}\n\n"
    )

    if not rows:
        return result + "Пока сообщений нет 😄"

    result += "\n".join(
        stats_lines(rows)
    )

    total_messages, _, _ = get_group_totals(
        chat_id,
        today,
        today
    )

    result += (
        f"\n\n"
        f"💬 Всего сообщений: "
        f"{format_number(total_messages)}"
    )

    return result




# ============================================================
# СТАТИСТИКА ЗА ПЕРИОД
# ============================================================

def make_period_stats_text(
    chat_id,
    title,
    start_date,
    end_date
):
    rows = get_stats(
        chat_id,
        start_date,
        end_date
    )

    result = (
        f"{title}\n\n"
        f"📅 {format_period(start_date, end_date)}"
        f"\n\n"
    )

    if not rows:
        return result + "Пока статистики нет 😄"

    result += "\n".join(
        stats_lines(rows)
    )

    messages, _, users = get_group_totals(
        chat_id,
        start_date,
        end_date
    )

    result += (
        f"\n\n"
        f"👥 Активных участников: "
        f"{format_number(users)}"
        f"\n"
        f"💬 Всего сообщений: "
        f"{format_number(messages)}"
    )

    return result




# ============================================================
# /START
# ============================================================

# ============================================================
# /START
# ============================================================

# ============================================================
# /HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await start(
        update,
        context
    )


# ============================================================
# /STATS
# ============================================================

async def stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not group_only(update):

        await update.message.reply_text(
            "Эту команду нужно "
            "использовать в группе."
        )

        return

    await update.message.reply_text(
        make_today_stats_text(
            update.effective_chat.id
        )
    )


# ============================================================
# /ME
# ============================================================

async def me(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not group_only(update):
        await update.message.reply_text(
            "Эту команду нужно использовать в группе."
        )
        return

    chat_id = update.effective_chat.id
    user = update.effective_user

    today = moscow_now().date()
    start_7, _ = last_n_days(7)
    start_month, end_month = current_month_range()

    messages, _, _, _, _ = get_user_stats(
        chat_id,
        user.id,
        today,
        today
    )

    week_messages, _, _, _, _ = get_user_stats(
        chat_id,
        user.id,
        start_7,
        today
    )

    month_messages, _, _, _, _ = get_user_stats(
        chat_id,
        user.id,
        start_month,
        end_month
    )

    rank = get_rank(
        chat_id,
        user.id,
        today,
        today
    )

    current_streak, best_streak = get_streaks(
        chat_id,
        user.id
    )

    night = get_night_messages(
        chat_id,
        user.id
    )

    text = (
        f"👤 {user.full_name}\n\n"
        f"📅 Сегодня: {format_number(messages)} сообщений\n"
        f"🏆 Место сегодня: {rank if rank else '—'}\n\n"
        f"📆 За 7 дней: {format_number(week_messages)} сообщений\n"
        f"🗓 За месяц: {format_number(month_messages)} сообщений\n\n"
        f"🔥 Серия сейчас: {current_streak} дн.\n"
        f"🏅 Лучшая серия: {best_streak} дн.\n"
        f"🌙 Ночных сообщений: {format_number(night)}"
    )

    await update.message.reply_text(text)




# ============================================================
# /WEEK
# ============================================================

async def week(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not group_only(update):

        await update.message.reply_text(
            "Эту команду нужно "
            "использовать в группе."
        )

        return

    start_date, end_date = (
        last_n_days(7)
    )

    await update.message.reply_text(

        make_period_stats_text(
            update.effective_chat.id,
            "🏆 Топ за 7 дней",
            start_date,
            end_date
        )
    )


# ============================================================
# /MONTH
# ============================================================

async def month(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not group_only(update):

        await update.message.reply_text(
            "Эту команду нужно "
            "использовать в группе."
        )

        return

    start_date, end_date = (
        current_month_range()
    )

    await update.message.reply_text(

        make_period_stats_text(
            update.effective_chat.id,
            "🏆 Топ за месяц",
            start_date,
            end_date
        )
    )


# ============================================================
# /GROUP
# ============================================================

async def group(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not group_only(update):
        await update.message.reply_text(
            "Эту команду нужно использовать в группе."
        )
        return

    chat_id = update.effective_chat.id

    start_date = date(2000, 1, 1)
    end_date = moscow_now().date()

    messages, _, users = get_group_totals(
        chat_id,
        start_date,
        end_date
    )

    top = get_stats(
        chat_id,
        start_date,
        end_date
    )[:1]

    text = (
        "📊 Наша группа\n\n"
        f"👥 Активных участников: {format_number(users)}\n"
        f"💬 Сообщений за всё время: {format_number(messages)}"
    )

    if top:
        text += (
            "\n\n"
            "👑 Самый активный участник:\n"
            f"{top[0][1]} — {format_number(top[0][2])} сообщений"
        )

    await update.message.reply_text(text)




# ============================================================
# /ACTIVITY
# ============================================================

async def activity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not group_only(update):
        await update.message.reply_text(
            "Эту команду нужно использовать в группе."
        )
        return

    chat_id = update.effective_chat.id
    start_date, end_date = last_n_days(7)

    rows = get_daily_group_activity(
        chat_id,
        start_date,
        end_date
    )

    by_date = {
        row[0]: row[1]
        for row in rows
    }

    max_messages = max(
        by_date.values(),
        default=0
    )

    lines = [
        "📈 Активность группы за 7 дней",
        ""
    ]

    for i in range(7):
        d = start_date + timedelta(days=i)
        messages = by_date.get(
            date_str(d),
            0
        )

        lines.append(
            f"{d.strftime('%a %d.%m')}: "
            f"{bar(messages, max_messages)} "
            f"{format_number(messages)} сообщений"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )




# ============================================================
# /HOURS
# ============================================================

async def hours(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not group_only(update):
        await update.message.reply_text(
            "Эту команду нужно использовать в группе."
        )
        return

    chat_id = update.effective_chat.id
    start_date, end_date = last_n_days(7)

    rows = get_hourly_activity(
        chat_id,
        start_date,
        end_date
    )

    by_hour = {
        row[0]: row[1]
        for row in rows
    }

    max_messages = max(
        by_hour.values(),
        default=0
    )

    lines = [
        "🕐 Активность по часам за 7 дней",
        ""
    ]

    for hour in range(24):
        messages = by_hour.get(hour, 0)

        lines.append(
            f"{hour:02d}:00 "
            f"{bar(messages, max_messages, 12)} "
            f"{format_number(messages)} сообщений"
        )

    await update.message.reply_text(
        "\n".join(lines)
    )




# ============================================================
# /ACHIEVEMENTS
# ============================================================

async def achievements(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not group_only(update):
        await update.message.reply_text(
            "Эту команду нужно использовать в группе."
        )
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    today = moscow_now().date()

    messages, _, _, _, _ = get_user_stats(
        chat_id,
        user.id,
        date(2000, 1, 1),
        today
    )

    current_streak, best_streak = get_streaks(
        chat_id,
        user.id
    )

    night = get_night_messages(
        chat_id,
        user.id
    )

    achievements_list = []

    if messages >= 100:
        achievements_list.append(
            "💬 Разговорился — 100 сообщений"
        )

    if messages >= 500:
        achievements_list.append(
            "🔥 Не замолкает — 500 сообщений"
        )

    if messages >= 1000:
        achievements_list.append(
            "⚡ Марафонец — 1 000 сообщений"
        )

    if messages >= 5000:
        achievements_list.append(
            "🚀 Турборежим — 5 000 сообщений"
        )

    if night >= 100:
        achievements_list.append(
            "🌙 Ночной житель — 100 ночных сообщений"
        )

    if best_streak >= 7:
        achievements_list.append(
            "🔥 Серия — 7 дней подряд"
        )

    if best_streak >= 30:
        achievements_list.append(
            "🔥🔥 Месяц без молчания — 30 дней подряд"
        )

    if not achievements_list:
        text = (
            "🏅 Достижения\n\n"
            "Пока достижений нет. "
            "Отправляй сообщения 😄"
        )
    else:
        text = (
            "🏅 Твои достижения\n\n"
            + "\n".join(achievements_list)
        )

    await update.message.reply_text(text)




# ============================================================
# /RECORDS
# ============================================================

async def records(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not group_only(update):
        await update.message.reply_text(
            "Эту команду нужно использовать в группе."
        )
        return

    _, best_messages, _ = get_best_records(
        update.effective_chat.id
    )

    lines = [
        "🏆 Рекорд группы",
        ""
    ]

    if best_messages:
        lines.append(
            "💬 Больше всего сообщений за день:"
        )
        lines.append(
            f"{best_messages[0]} — "
            f"{format_number(best_messages[1])} сообщений "
            f"({best_messages[2]})"
        )
    else:
        lines.append("Рекордов пока нет 😄")

    await update.message.reply_text(
        "\n".join(lines)
    )




# ============================================================
# /FACT
# ============================================================

async def fact(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not group_only(update):
        await update.message.reply_text(
            "Эту команду нужно использовать в группе."
        )
        return

    import random

    chat_id = update.effective_chat.id
    start_date = date(2000, 1, 1)
    end_date = moscow_now().date()

    rows = get_stats(
        chat_id,
        start_date,
        end_date
    )

    if not rows:
        await update.message.reply_text(
            "🤔 Пока мало данных для фактов."
        )
        return

    facts = []

    top = rows[0]
    facts.append(
        f"👑 {top[1]} — самый активный участник: "
        f"{format_number(top[2])} сообщений за всё время."
    )

    messages, _, users = get_group_totals(
        chat_id,
        start_date,
        end_date
    )

    if users:
        facts.append(
            f"👥 В статистике группы уже есть "
            f"{format_number(users)} активных участников."
        )

    facts.append(
        f"💬 Всего группа отправила "
        f"{format_number(messages)} сообщений."
    )

    await update.message.reply_text(
        "🎲 Факт дня\n\n"
        + random.choice(facts)
    )




# ============================================================
# ОБРАБОТКА СООБЩЕНИЙ
# ============================================================

async def count_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message

    if not message:
        return

    if message.chat.type not in (
        "group",
        "supergroup"
    ):
        return

    if not message.from_user:
        return

    if message.from_user.is_bot:
        return

    # Любая пересылка не считается.
    if getattr(message, "forward_origin", None) is not None:
        return

    # Автоматические пересылки из каналов в привязанную группу не считаются.
    if getattr(message, "is_automatic_forward", False):
        return

    # Совместимость со старыми версиями Bot API.
    if (
        getattr(message, "forward_from", None) is not None
        or getattr(message, "forward_from_chat", None) is not None
        or getattr(message, "forward_sender_name", None) is not None
    ):
        return

    # Команды не считаются.
    if (
        message.text
        and message.text.lstrip().startswith("/")
    ):
        return

    # Сообщения с содержимым пользователя.
    content_fields = (
        "text",
        "caption",
        "audio",
        "document",
        "animation",
        "game",
        "photo",
        "sticker",
        "video",
        "voice",
        "video_note",
        "contact",
        "location",
        "venue",
        "poll",
        "dice",
        "story",
        "paid_media",
        "checklist",
    )

    if not any(
        getattr(message, field, None) is not None
        for field in content_fields
    ):
        return

    message_time = message.date.astimezone(MOSCOW)

    save_message(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        user_name=message.from_user.full_name,
        hour=message_time.hour,
    )




# ============================================================
# АВТОМАТИЧЕСКИЙ ОТЧЁТ В 20:30
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

            # ВАЖНО:
            # здесь остаётся именно статистика
            # ТЕКУЩЕГО дня на момент 20:00.

            text = make_today_stats_text(
                chat_id
            )

            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                disable_notification=True,
            )

            print(
                f"Отчёт отправлен "
                f"в чат {chat_id}"
            )

        except Exception as error:

            print(
                f"Ошибка отправки "
                f"в {chat_id}: {error}"
            )


# ============================================================
# КОМАНДЫ TELEGRAM
# ============================================================

BOT_COMMANDS = [
    BotCommand("start", "Запустить бота"),
    BotCommand("stats", "Статистика за сегодня"),
    BotCommand("me", "Моя статистика"),
    BotCommand("week", "Статистика за 7 дней"),
    BotCommand("month", "Статистика за месяц"),
    BotCommand("group", "Статистика группы"),
    BotCommand("activity", "Активность по дням"),
    BotCommand("hours", "Активность по часам"),
    BotCommand("achievements", "Достижения"),
    BotCommand("records", "Рекорды"),
    BotCommand("fact", "Случайный факт"),
    BotCommand("help", "Помощь"),
]


async def refresh_existing_group_menus(application: Application):
    """
    После перезапуска бота обновляет Reply Keyboard во всех уже известных
    группах. Это нужно, чтобы Telegram получил новую клавиатуру без старого
    input_field_placeholder (например, «Выбери раздел 👇»).
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT chat_id FROM bot_menu_messages")
    chat_ids = [row[0] for row in cursor.fetchall()]
    db.close()

    for chat_id in chat_ids:
        try:
            await send_group_menu(
                application.bot,
                chat_id,
                "🤖 МЕНЮ БОТА\n\n"
                "Кнопки меню доступны над строкой ввода 👇",
                replace_existing=True,
            )
        except Exception as error:
            print(
                f"Не удалось обновить меню в чате {chat_id}: {error}"
            )


async def post_init(application: Application):
    """
    Инициализация бота без Reply Keyboard и без принудительного
    системного меню Telegram.
    """
    # Команды можно использовать вручную: /start, /stats, /me и т.д.
    # Кнопка постоянного меню Telegram здесь специально не настраивается.
    await application.bot.set_my_commands([])

    print("Меню и Reply Keyboard отключены.")


# ============================================================
# ЗАПУСК
# ============================================================

def main():

    init_database()

    if TOKEN == "PASTE_NEW_TOKEN_HERE":

        raise RuntimeError(
            "Не задан BOT_TOKEN. "
            "Установи переменную окружения BOT_TOKEN."
        )

    app = (
        Application
        .builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    # --------------------------------------------------------
    # КОМАНДЫ
    # --------------------------------------------------------

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    app.add_handler(
        CommandHandler(
            "stats",
            stats
        )
    )

    app.add_handler(
        CommandHandler(
            "me",
            me
        )
    )

    app.add_handler(
        CommandHandler(
            "week",
            week
        )
    )

    app.add_handler(
        CommandHandler(
            "month",
            month
        )
    )

    app.add_handler(
        CommandHandler(
            "group",
            group
        )
    )

    app.add_handler(
        CommandHandler(
            "activity",
            activity
        )
    )

    app.add_handler(
        CommandHandler(
            "hours",
            hours
        )
    )

    app.add_handler(
        CommandHandler(
            "achievements",
            achievements
        )
    )

    app.add_handler(
        CommandHandler(
            "records",
            records
        )
    )

    app.add_handler(
        CommandHandler(
            "fact",
            fact
        )
    )

    # --------------------------------------------------------
    # СООБЩЕНИЯ
    # --------------------------------------------------------

    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND,
            count_message
        )
    )

    # --------------------------------------------------------
    # ОТЧЁТ В 20:30 ПО МОСКВЕ
    # --------------------------------------------------------

    app.job_queue.run_daily(

        daily_report,

        time=time(
            hour=20,
            minute=30,
            tzinfo=MOSCOW
        )
    )

    print(
        "================================"
    )

    print(
        "Бот запущен!"
    )

    print(
        "Часовой пояс: Москва (UTC+3)"
    )

    print(
        "Ежедневный отчёт: 20:30"
    )

    print(
        "Постоянное меню Reply Keyboard: отключено"
    )

    print(
        "Пересланные сообщения: НЕ считаются"
    )

    print(
        "================================"
    )

    app.run_polling()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    main()

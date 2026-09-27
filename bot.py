import os
import re
import sqlite3
import random

from datetime import datetime, time, timezone, timedelta, date

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode

from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

# НЕ вставляй токен прямо в код.
#
# Linux/macOS:
# export BOT_TOKEN="твой_новый_токен"
#
# Windows PowerShell:
# $env:BOT_TOKEN="твой_новый_токен"

TOKEN = os.getenv(
    "BOT_TOKEN",
    "PASTE_NEW_TOKEN_HERE"
)

# Москва = UTC+3
MOSCOW = timezone(
    timedelta(hours=3)
)

DATABASE = "stats.db"


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_db():

    db = sqlite3.connect(
        DATABASE,
        timeout=30
    )

    db.execute(
        "PRAGMA journal_mode=WAL"
    )

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

    # --------------------------------------------------------
    # ИНДЕКСЫ
    # --------------------------------------------------------

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

    return datetime.now(
        MOSCOW
    )


def today_str():

    return moscow_now().strftime(
        "%Y-%m-%d"
    )


def date_str(value):

    return value.strftime(
        "%Y-%m-%d"
    )


def pretty_date(value):

    return value.strftime(
        "%d.%m.%Y"
    )


def last_n_days(n):

    today = moscow_now().date()

    start = today - timedelta(
        days=n - 1
    )

    return start, today


def current_month_range():

    today = moscow_now().date()

    start = today.replace(
        day=1
    )

    return start, today


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

def save_message(
    chat_id,
    user_id,
    user_name,
    words,
    characters,
    hour
):

    today = today_str()

    db = get_db()
    cursor = db.cursor()

    # --------------------------------------------------------
    # ДНЕВНАЯ СТАТИСТИКА
    # --------------------------------------------------------

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
            ?, ?,
            ?, ?
        )

        ON CONFLICT(
            chat_id,
            user_id,
            date
        )

        DO UPDATE SET

            user_name =
                excluded.user_name,

            messages =
                messages + 1,

            words =
                words + excluded.words,

            characters =
                characters + excluded.characters,

            longest_message_words =
                MAX(
                    longest_message_words,
                    excluded.longest_message_words
                ),

            longest_message_chars =
                MAX(
                    longest_message_chars,
                    excluded.longest_message_chars
                )
    """, (
        chat_id,
        user_id,
        user_name,
        today,
        words,
        characters,
        words,
        characters,
    ))

    # --------------------------------------------------------
    # СТАТИСТИКА ПО ЧАСАМ
    # --------------------------------------------------------

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
            ?
        )

        ON CONFLICT(
            chat_id,
            user_id,
            date,
            hour
        )

        DO UPDATE SET

            messages =
                messages + 1,

            words =
                words + excluded.words

    """, (
        chat_id,
        user_id,
        today,
        hour,
        words,
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

            MAX(user_name)
                AS user_name,

            SUM(messages)
                AS messages,

            SUM(words)
                AS words,

            SUM(characters)
                AS characters,

            MAX(longest_message_words)
                AS longest_message_words,

            MAX(longest_message_chars)
                AS longest_message_chars

        FROM daily_stats

        WHERE chat_id = ?

          AND date BETWEEN ?
          AND ?

        GROUP BY user_id

        ORDER BY
            words DESC,
            messages DESC

    """, (
        chat_id,
        date_str(start_date),
        date_str(end_date)
    ))

    rows = cursor.fetchall()

    db.close()

    return rows


def get_user_stats(
    chat_id,
    user_id,
    start_date,
    end_date
):

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT

            COALESCE(
                SUM(messages),
                0
            ),

            COALESCE(
                SUM(words),
                0
            ),

            COALESCE(
                SUM(characters),
                0
            ),

            COALESCE(
                MAX(longest_message_words),
                0
            ),

            COALESCE(
                MAX(longest_message_chars),
                0
            )

        FROM daily_stats

        WHERE chat_id = ?
          AND user_id = ?
          AND date BETWEEN ?
          AND ?

    """, (
        chat_id,
        user_id,
        date_str(start_date),
        date_str(end_date)
    ))

    row = cursor.fetchone()

    db.close()

    return row


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

    for position, row in enumerate(
        rows,
        start=1
    ):

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

            COALESCE(
                SUM(messages),
                0
            ),

            COALESCE(
                SUM(words),
                0
            ),

            COUNT(
                DISTINCT user_id
            )

        FROM daily_stats

        WHERE chat_id = ?
          AND date BETWEEN ?
          AND ?

    """, (
        chat_id,
        date_str(start_date),
        date_str(end_date)
    ))

    row = cursor.fetchone()

    db.close()

    return row


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

            SUM(messages),

            SUM(words)

        FROM daily_stats

        WHERE chat_id = ?

          AND date BETWEEN ?
          AND ?

        GROUP BY date

        ORDER BY date

    """, (
        chat_id,
        date_str(start_date),
        date_str(end_date)
    ))

    rows = cursor.fetchall()

    db.close()

    return rows


# ============================================================
# АКТИВНОСТЬ ПО ЧАСАМ
# ============================================================

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

            SUM(messages),

            SUM(words)

        FROM hourly_stats

        WHERE chat_id = ?

          AND date BETWEEN ?
          AND ?

        GROUP BY hour

        ORDER BY hour

    """, (
        chat_id,
        date_str(start_date),
        date_str(end_date)
    ))

    rows = cursor.fetchall()

    db.close()

    return rows


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

    # Больше всего слов за день

    cursor.execute("""
        SELECT
            user_name,
            words,
            date

        FROM daily_stats

        WHERE chat_id = ?

        ORDER BY words DESC

        LIMIT 1
    """, (chat_id,))

    best_words = cursor.fetchone()

    # Больше всего сообщений за день

    cursor.execute("""
        SELECT
            user_name,
            messages,
            date

        FROM daily_stats

        WHERE chat_id = ?

        ORDER BY messages DESC

        LIMIT 1
    """, (chat_id,))

    best_messages = cursor.fetchone()

    # Самое длинное сообщение

    cursor.execute("""
        SELECT
            user_name,
            longest_message_words,
            longest_message_chars,
            date

        FROM daily_stats

        WHERE chat_id = ?

        ORDER BY
            longest_message_words DESC,
            longest_message_chars DESC

        LIMIT 1
    """, (chat_id,))

    longest_message = cursor.fetchone()

    db.close()

    return (
        best_words,
        best_messages,
        longest_message
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
    # ТЕКУЩАЯ СЕРИЯ
    # --------------------------------------------------------

    current = 0

    cursor_date = today

    while cursor_date in date_set:

        current += 1

        cursor_date -= timedelta(
            days=1
        )

    # --------------------------------------------------------
    # ЛУЧШАЯ СЕРИЯ
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

        user_name = row[1]
        messages = row[2]
        words = row[3]

        line = (
            f"{medal(position)} "
            f"{user_name} — "
            f"{format_number(words)} слов"
        )

        if show_messages:

            line += (
                f" · "
                f"{format_number(messages)} сообщ."
            )

        lines.append(line)

    return lines


def group_only(update):

    return (
        update.effective_chat
        and
        update.effective_chat.type
        in (
            "group",
            "supergroup"
        )
    )


# ============================================================
# INLINE UI / STATS
# ============================================================

def stats_keyboard(active="today"):

    labels = {
        "today": "👤 Я",
        "week": "🏆 Неделя",
        "month": "🗓 Месяц",
        "group": "📊 Группа",
        "achievements": "🔥 Достижения",
    }

    def label(key):

        if key == active:
            return f"● {labels[key]}"

        return labels[key]

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                label("today"),
                callback_data="stats:today"
            ),
            InlineKeyboardButton(
                label("week"),
                callback_data="stats:week"
            ),
            InlineKeyboardButton(
                label("month"),
                callback_data="stats:month"
            ),
        ],
        [
            InlineKeyboardButton(
                label("group"),
                callback_data="stats:group"
            ),
            InlineKeyboardButton(
                label("achievements"),
                callback_data="stats:achievements"
            ),
        ],
    ])


def stats_header(chat_title=None):

    title = (
        chat_title
        if chat_title
        else "Статистика"
    )

    return (
        f"📊 <b>{title}</b>\n"
        f"<i>Статистика активности</i>"
    )


# ============================================================
# КАРТОЧКА «Я»
# ============================================================

def make_today_card(
    chat_id,
    user_id,
    user_name,
    chat_title=None
):

    today = moscow_now().date()

    (
        messages,
        words,
        characters,
        longest_words,
        longest_chars
    ) = get_user_stats(
        chat_id,
        user_id,
        today,
        today
    )

    rank = get_rank(
        chat_id,
        user_id,
        today,
        today
    )

    current_streak, best_streak = get_streaks(
        chat_id,
        user_id
    )

    avg = (
        words / messages
        if messages
        else 0
    )

    total_messages, total_words, total_users = (
        get_group_totals(
            chat_id,
            today,
            today
        )
    )

    rank_text = (
        f"#{rank}"
        if rank
        else "—"
    )

    return (
        f"{stats_header(chat_title)}\n\n"

        f"👤 <b>{user_name}</b>\n"
        f"📅 Сегодня · {pretty_date(today)}\n\n"

        f"📝 <b>{format_number(words)}</b> слов\n"
        f"💬 <b>{format_number(messages)}</b> сообщений\n"
        f"📊 <b>{avg:.1f}</b> слов / сообщение\n\n"

        f"🏆 Место: <b>{rank_text}</b>\n"
        f"🔥 Серия: <b>{current_streak} дн.</b>\n"
        f"🏅 Рекорд серии: <b>{best_streak} дн.</b>\n\n"

        f"📜 Самое длинное: "
        f"<b>{format_number(longest_words)}</b> слов\n\n"

        f"👥 Сегодня активны: "
        f"<b>{format_number(total_users)}</b>\n"
        f"💬 В группе: "
        f"<b>{format_number(total_messages)}</b> сообщений"
    )


# ============================================================
# КАРТОЧКА «НЕДЕЛЯ»
# ============================================================

def make_week_card(
    chat_id,
    chat_title=None
):

    start_date, end_date = last_n_days(7)

    rows = get_stats(
        chat_id,
        start_date,
        end_date
    )

    messages, words, users = get_group_totals(
        chat_id,
        start_date,
        end_date
    )

    result = (
        f"{stats_header(chat_title)}\n\n"
        f"🏆 <b>Топ за 7 дней</b>\n"
        f"📅 {format_period(start_date, end_date)}\n\n"
    )

    if not rows:

        return (
            result +
            "📭 Пока нет статистики."
        )

    medals = [
        "🥇",
        "🥈",
        "🥉"
    ]

    for position, row in enumerate(
        rows[:10],
        start=1
    ):

        user_name = row[1]
        user_messages = row[2]
        user_words = row[3]

        icon = (
            medals[position - 1]
            if position <= 3
            else f"<b>{position}.</b>"
        )

        result += (
            f"{icon} <b>{user_name}</b>\n"
            f"   📝 {format_number(user_words)} слов"
            f" · 💬 {format_number(user_messages)}\n"
        )

    result += (
        f"\n━━━━━━━━━━━━━━\n"
        f"👥 Участников: <b>{format_number(users)}</b>\n"
        f"💬 Сообщений: <b>{format_number(messages)}</b>\n"
        f"📝 Слов: <b>{format_number(words)}</b>"
    )

    return result


# ============================================================
# КАРТОЧКА «МЕСЯЦ»
# ============================================================

def make_month_card(
    chat_id,
    chat_title=None
):

    start_date, end_date = current_month_range()

    rows = get_stats(
        chat_id,
        start_date,
        end_date
    )

    messages, words, users = get_group_totals(
        chat_id,
        start_date,
        end_date
    )

    result = (
        f"{stats_header(chat_title)}\n\n"
        f"🗓 <b>Топ месяца</b>\n"
        f"📅 {format_period(start_date, end_date)}\n\n"
    )

    if not rows:

        return (
            result +
            "📭 В этом месяце пока нет статистики."
        )

    medals = [
        "🥇",
        "🥈",
        "🥉"
    ]

    for position, row in enumerate(
        rows[:10],
        start=1
    ):

        user_name = row[1]
        user_messages = row[2]
        user_words = row[3]

        icon = (
            medals[position - 1]
            if position <= 3
            else f"<b>{position}.</b>"
        )

        result += (
            f"{icon} <b>{user_name}</b>\n"
            f"   📝 {format_number(user_words)} слов"
            f" · 💬 {format_number(user_messages)}\n"
        )

    result += (
        f"\n━━━━━━━━━━━━━━\n"
        f"👥 Участников: <b>{format_number(users)}</b>\n"
        f"💬 Сообщений: <b>{format_number(messages)}</b>\n"
        f"📝 Слов: <b>{format_number(words)}</b>"
    )

    return result


# ============================================================
# КАРТОЧКА «ГРУППА»
# ============================================================

def make_group_card(
    chat_id,
    chat_title=None
):

    start_date = date(
        2000,
        1,
        1
    )

    end_date = moscow_now().date()

    messages, words, users = get_group_totals(
        chat_id,
        start_date,
        end_date
    )

    rows = get_stats(
        chat_id,
        start_date,
        end_date
    )

    result = (
        f"{stats_header(chat_title)}\n\n"

        f"📊 <b>Группа за всё время</b>\n\n"

        f"👥 Участников: "
        f"<b>{format_number(users)}</b>\n"

        f"💬 Сообщений: "
        f"<b>{format_number(messages)}</b>\n"

        f"📝 Слов: "
        f"<b>{format_number(words)}</b>\n"
    )

    if rows:

        result += (
            "\n━━━━━━━━━━━━━━\n"
            "👑 <b>Топ-5 участников</b>\n\n"
        )

        medals = [
            "🥇",
            "🥈",
            "🥉"
        ]

        for position, row in enumerate(
            rows[:5],
            start=1
        ):

            icon = (
                medals[position - 1]
                if position <= 3
                else f"{position}."
            )

            result += (
                f"{icon} <b>{row[1]}</b>\n"
                f"   📝 {format_number(row[3])} слов\n"
            )

    return result


# ============================================================
# КАРТОЧКА «ДОСТИЖЕНИЯ»
# ============================================================

def make_achievements_card(
    chat_id,
    user_id,
    user_name,
    chat_title=None
):

    start_date = date(
        2000,
        1,
        1
    )

    end_date = moscow_now().date()

    (
        messages,
        words,
        characters,
        longest_words,
        longest_chars
    ) = get_user_stats(
        chat_id,
        user_id,
        start_date,
        end_date
    )

    current_streak, best_streak = get_streaks(
        chat_id,
        user_id
    )

    night = get_night_messages(
        chat_id,
        user_id
    )

    achievements_list = []

    # --------------------------------------------------------
    # СЛОВА
    # --------------------------------------------------------

    if words >= 10_000:

        achievements_list.append(
            (
                "🗣️",
                "Болтун",
                "10 000 слов"
            )
        )

    if words >= 50_000:

        achievements_list.append(
            (
                "📚",
                "Писатель",
                "50 000 слов"
            )
        )

    if words >= 100_000:

        achievements_list.append(
            (
                "📖",
                "Летописец",
                "100 000 слов"
            )
        )

    # --------------------------------------------------------
    # СООБЩЕНИЯ
    # --------------------------------------------------------

    if messages >= 500:

        achievements_list.append(
            (
                "💬",
                "Не замолкает",
                "500 сообщений"
            )
        )

    if messages >= 1_000:

        achievements_list.append(
            (
                "⚡",
                "Марафонец",
                "1 000 сообщений"
            )
        )

    # --------------------------------------------------------
    # ДЛИННЫЕ СООБЩЕНИЯ
    # --------------------------------------------------------

    if longest_words >= 1_000:

        achievements_list.append(
            (
                "📜",
                "Простыня",
                "1 000+ слов в сообщении"
            )
        )

    # --------------------------------------------------------
    # НОЧЬ
    # --------------------------------------------------------

    if night >= 100:

        achievements_list.append(
            (
                "🌙",
                "Ночной житель",
                "100 ночных сообщений"
            )
        )

    # --------------------------------------------------------
    # СЕРИЯ
    # --------------------------------------------------------

    if best_streak >= 7:

        achievements_list.append(
            (
                "🔥",
                "Серия",
                "7 дней подряд"
            )
        )

    if best_streak >= 30:

        achievements_list.append(
            (
                "🔥🔥",
                "Месяц без молчания",
                "30 дней подряд"
            )
        )

    result = (
        f"{stats_header(chat_title)}\n\n"

        f"🔥 <b>Достижения</b>\n"
        f"👤 {user_name}\n\n"
    )

    if not achievements_list:

        return (
            result +
            "🔒 Пока открытых достижений нет.\n\n"
            "Продолжай общаться — "
            "они появятся здесь."
        )

    for (
        icon,
        title,
        description
    ) in achievements_list:

        result += (
            f"{icon} <b>{title}</b>\n"
            f"   {description}\n\n"
        )

    result += (
        f"━━━━━━━━━━━━━━\n"
        f"🏅 Открыто: "
        f"<b>{len(achievements_list)}</b>"
    )

    return result


# ============================================================
# ВЫБОР КАРТОЧКИ
# ============================================================

def build_stats_view(
    view,
    chat_id,
    user_id,
    user_name,
    chat_title=None
):

    if view == "today":

        return make_today_card(
            chat_id,
            user_id,
            user_name,
            chat_title
        )

    if view == "week":

        return make_week_card(
            chat_id,
            chat_title
        )

    if view == "month":

        return make_month_card(
            chat_id,
            chat_title
        )

    if view == "group":

        return make_group_card(
            chat_id,
            chat_title
        )

    if view == "achievements":

        return make_achievements_card(
            chat_id,
            user_id,
            user_name,
            chat_title
        )

    return make_today_card(
        chat_id,
        user_id,
        user_name,
        chat_title
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
        f"📅 <b>Статистика за сегодня</b>\n\n"
        f"{pretty_date(today)}\n\n"
    )

    if not rows:

        return (
            result +
            "Пока никто ничего "
            "не напиздел 😄"
        )

    total_messages, total_words, _ = (
        get_group_totals(
            chat_id,
            today,
            today
        )
    )

    result += "\n".join(
        stats_lines(rows)
    )

    result += (
        f"\n\n"
        f"💬 Всего сообщений: "
        f"{format_number(total_messages)}\n"
        f"📝 Всего слов: "
        f"{format_number(total_words)}"
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
        f"📅 "
        f"{format_period(start_date, end_date)}"
        f"\n\n"
    )

    if not rows:

        return (
            result +
            "Пока статистики нет 😄"
        )

    result += "\n".join(
        stats_lines(
            rows,
            show_messages=True
        )
    )

    messages, words, users = (
        get_group_totals(
            chat_id,
            start_date,
            end_date
        )
    )

    result += (
        f"\n\n"
        f"👥 Активных участников: "
        f"{format_number(users)}\n"
        f"💬 Сообщений: "
        f"{format_number(messages)}\n"
        f"📝 Слов: "
        f"{format_number(words)}"
    )

    return result


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(

        "Привет! 🤖\n\n"

        "Я считаю, кто сколько "
        "напиздел в чате.\n\n"

        "/stats — интерактивная статистика\n"
        "/me — твоя статистика\n"
        "/week — последние 7 дней\n"
        "/month — текущий месяц\n"
        "/group — статистика группы\n"
        "/activity — активность по дням\n"
        "/hours — активность по часам\n"
        "/achievements — достижения\n"
        "/records — рекорды\n"
        "/fact — случайный факт\n"
        "/help — список команд"
    )


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

    chat = update.effective_chat
    user = update.effective_user

    text = build_stats_view(
        "today",
        chat.id,
        user.id,
        user.full_name,
        chat.title
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=stats_keyboard("today")
    )


# ============================================================
# INLINE CALLBACKS /STATS
# ============================================================

async def stats_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if not query.message:
        return

    data = query.data or ""

    if not data.startswith("stats:"):
        return

    if query.message.chat.type not in (
        "group",
        "supergroup"
    ):
        return

    view = data.split(
        ":",
        1
    )[1]

    allowed_views = {
        "today",
        "week",
        "month",
        "group",
        "achievements",
    }

    if view not in allowed_views:
        return

    chat = query.message.chat
    user = query.from_user

    text = build_stats_view(
        view,
        chat.id,
        user.id,
        user.full_name,
        chat.title
    )

    try:

        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=stats_keyboard(view)
        )

    except Exception as error:

        # Например, если пользователь нажал
        # на уже открытый раздел повторно.
        if "Message is not modified" not in str(error):

            print(
                f"Ошибка stats callback: {error}"
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
            "Эту команду нужно "
            "использовать в группе."
        )

        return

    chat_id = update.effective_chat.id
    user = update.effective_user

    today = moscow_now().date()

    start_7, _ = last_n_days(7)

    start_month, end_month = (
        current_month_range()
    )

    # --------------------------------------------------------
    # Сегодня
    # --------------------------------------------------------

    (
        messages,
        words,
        characters,
        longest_words,
        longest_chars
    ) = get_user_stats(
        chat_id,
        user.id,
        today,
        today
    )

    rank = get_rank(
        chat_id,
        user.id,
        today,
        today
    )

    # --------------------------------------------------------
    # Неделя
    # --------------------------------------------------------

    (
        week_messages,
        week_words,
        _,
        _,
        _
    ) = get_user_stats(
        chat_id,
        user.id,
        start_7,
        today
    )

    # --------------------------------------------------------
    # Месяц
    # --------------------------------------------------------

    (
        month_messages,
        month_words,
        _,
        _,
        _
    ) = get_user_stats(
        chat_id,
        user.id,
        start_month,
        end_month
    )

    # --------------------------------------------------------
    # Серии
    # --------------------------------------------------------

    current_streak, best_streak = (
        get_streaks(
            chat_id,
            user.id
        )
    )

    # --------------------------------------------------------
    # Ночь
    # --------------------------------------------------------

    night = get_night_messages(
        chat_id,
        user.id
    )

    avg = (
        words / messages
        if messages
        else 0
    )

    text = (

        f"👤 {user.full_name}\n\n"

        f"📅 Сегодня:\n"
        f"📝 {format_number(words)} слов\n"
        f"💬 {format_number(messages)} сообщений\n"
        f"📊 {avg:.1f} слов/сообщение\n"
        f"🏆 Место: "
        f"{rank if rank else '—'}\n\n"

        f"📆 За 7 дней: "
        f"{format_number(week_words)} слов / "
        f"{format_number(week_messages)} сообщ.\n"

        f"🗓 За месяц: "
        f"{format_number(month_words)} слов / "
        f"{format_number(month_messages)} сообщ.\n\n"

        f"🔥 Серия сейчас: "
        f"{current_streak} дн.\n"

        f"🏅 Лучшая серия: "
        f"{best_streak} дн.\n"

        f"🌙 Ночных сообщений: "
        f"{format_number(night)}\n"

        f"📜 Самое длинное сегодня: "
        f"{format_number(longest_words)} слов"
    )

    await update.message.reply_text(
        text
    )


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

    start_date, end_date = last_n_days(7)

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
            "Эту команду нужно "
            "использовать в группе."
        )

        return

    chat_id = update.effective_chat.id

    start_date = date(
        2000,
        1,
        1
    )

    end_date = moscow_now().date()

    messages, words, users = (
        get_group_totals(
            chat_id,
            start_date,
            end_date
        )
    )

    top = get_stats(
        chat_id,
        start_date,
        end_date
    )[:1]

    text = (

        "📊 Наша группа\n\n"

        f"👥 Активных участников: "
        f"{format_number(users)}\n"

        f"💬 Сообщений за всё время: "
        f"{format_number(messages)}\n"

        f"📝 Слов за всё время: "
        f"{format_number(words)}\n"
    )

    if top:

        text += (
            "\n"
            "👑 Главный болтун:\n"
            f"{top[0][1]} — "
            f"{format_number(top[0][3])} слов"
        )

    await update.message.reply_text(
        text
    )


# ============================================================
# /ACTIVITY
# ============================================================

async def activity(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not group_only(update):

        await update.message.reply_text(
            "Эту команду нужно "
            "использовать в группе."
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
        row[0]: (
            row[1],
            row[2]
        )
        for row in rows
    }

    max_words = max(
        (
            value[1]
            for value in by_date.values()
        ),
        default=0
    )

    lines = [
        "📈 Активность группы за 7 дней",
        ""
    ]

    for i in range(7):

        d = (
            start_date
            +
            timedelta(days=i)
        )

        messages, words = (
            by_date.get(
                date_str(d),
                (0, 0)
            )
        )

        lines.append(

            f"{d.strftime('%a %d.%m')}: "
            f"{bar(words, max_words)} "
            f"{format_number(words)} слов"
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
            "Эту команду нужно "
            "использовать в группе."
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
        row[0]: (
            row[1],
            row[2]
        )
        for row in rows
    }

    max_words = max(
        (
            value[1]
            for value in by_hour.values()
        ),
        default=0
    )

    lines = [
        "🕐 Активность по часам за 7 дней",
        ""
    ]

    for hour in range(24):

        messages, words = (
            by_hour.get(
                hour,
                (0, 0)
            )
        )

        lines.append(

            f"{hour:02d}:00 "
            f"{bar(words, max_words, 12)} "
            f"{format_number(words)}"
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
            "Эту команду нужно "
            "использовать в группе."
        )

        return

    chat_id = update.effective_chat.id
    user = update.effective_user

    start_date = date(
        2000,
        1,
        1
    )

    end_date = moscow_now().date()

    (
        messages,
        words,
        characters,
        longest_words,
        longest_chars
    ) = get_user_stats(
        chat_id,
        user.id,
        start_date,
        end_date
    )

    current_streak, best_streak = (
        get_streaks(
            chat_id,
            user.id
        )
    )

    night = get_night_messages(
        chat_id,
        user.id
    )

    achievements_list = []

    if words >= 10_000:

        achievements_list.append(
            "🗣️ Болтун — 10 000 слов"
        )

    if words >= 50_000:

        achievements_list.append(
            "📚 Писатель — 50 000 слов"
        )

    if words >= 100_000:

        achievements_list.append(
            "📖 Летописец — 100 000 слов"
        )

    if messages >= 500:

        achievements_list.append(
            "💬 Не замолкает — "
            "500 сообщений"
        )

    if messages >= 1_000:

        achievements_list.append(
            "⚡ Марафонец — "
            "1 000 сообщений"
        )

    if longest_words >= 1_000:

        achievements_list.append(
            "📜 Простыня — "
            "сообщение на 1 000+ слов"
        )

    if night >= 100:

        achievements_list.append(
            "🌙 Ночной житель — "
            "100 ночных сообщений"
        )

    if best_streak >= 7:

        achievements_list.append(
            "🔥 Серия — "
            "7 дней подряд"
        )

    if best_streak >= 30:

        achievements_list.append(
            "🔥🔥 Месяц без молчания — "
            "30 дней подряд"
        )

    if not achievements_list:

        text = (
            "🏅 Достижения\n\n"
            "Пока достижений нет. "
            "Начинай напиздеть 😄"
        )

    else:

        text = (
            "🏅 Твои достижения\n\n"
            +
            "\n".join(
                achievements_list
            )
        )

    await update.message.reply_text(
        text
    )


# ============================================================
# /RECORDS
# ============================================================

async def records(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not group_only(update):

        await update.message.reply_text(
            "Эту команду нужно "
            "использовать в группе."
        )

        return

    (
        best_words,
        best_messages,
        longest_message
    ) = get_best_records(
        update.effective_chat.id
    )

    lines = [
        "🏆 Рекорды группы",
        ""
    ]

    # --------------------------------------------------------
    # РЕКОРД СЛОВ
    # --------------------------------------------------------

    if best_words:

        lines.append(

            "📝 Больше всего слов за день:\n"
            f"{best_words[0]} — "
            f"{format_number(best_words[1])} слов "
            f"({best_words[2]})"
        )

    # --------------------------------------------------------
    # РЕКОРД СООБЩЕНИЙ
    # --------------------------------------------------------

    if best_messages:

        lines.append(

            "\n💬 Больше всего сообщений за день:\n"
            f"{best_messages[0]} — "
            f"{format_number(best_messages[1])} сообщений "
            f"({best_messages[2]})"
        )

    # --------------------------------------------------------
    # САМОЕ ДЛИННОЕ
    # --------------------------------------------------------

    if longest_message:

        lines.append(

            "\n📜 Самое длинное сообщение:\n"
            f"{longest_message[0]} — "
            f"{format_number(longest_message[1])} слов / "
            f"{format_number(longest_message[2])} символов "
            f"({longest_message[3]})"
        )

    if len(lines) == 2:

        lines.append(
            "Рекордов пока нет 😄"
        )

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
            "Эту команду нужно "
            "использовать в группе."
        )

        return

    chat_id = update.effective_chat.id

    start_date = date(
        2000,
        1,
        1
    )

    end_date = moscow_now().date()

    rows = get_stats(
        chat_id,
        start_date,
        end_date
    )

    if not rows:

        await update.message.reply_text(
            "🤔 Пока мало данных "
            "для фактов."
        )

        return

    facts = []

    # --------------------------------------------------------
    # ГЛАВНЫЙ БОЛТУН
    # --------------------------------------------------------

    top = rows[0]

    facts.append(

        f"👑 {top[1]} — "
        f"главный болтун группы: "
        f"{format_number(top[3])} "
        f"слов за всё время."
    )

    # --------------------------------------------------------
    # САМЫЙ ДЛИННЫЙ ТЕКСТ
    # --------------------------------------------------------

    longest = max(
        rows,
        key=lambda row: row[5]
    )

    if longest[5]:

        facts.append(

            f"📜 {longest[1]} "
            f"однажды написал(а) "
            f"{format_number(longest[5])} "
            f"слов за день в одном сообщении."
        )

    # --------------------------------------------------------
    # УЧАСТНИКИ
    # --------------------------------------------------------

    messages, words, users = (
        get_group_totals(
            chat_id,
            start_date,
            end_date
        )
    )

    if users:

        facts.append(

            f"👥 В статистике группы "
            f"уже есть "
            f"{format_number(users)} "
            f"активных участников."
        )

    # --------------------------------------------------------
    # СООБЩЕНИЯ
    # --------------------------------------------------------

    if messages:

        facts.append(

            f"💬 Всего группа отправила "
            f"{format_number(messages)} "
            f"сообщений."
        )

    # --------------------------------------------------------
    # СЛОВА
    # --------------------------------------------------------

    if words:

        facts.append(

            f"📝 Всего группа написала "
            f"{format_number(words)} "
            f"слов."
        )

    await update.message.reply_text(

        "🎲 Факт дня\n\n"
        +
        random.choice(facts)
    )


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

    if not message.from_user:
        return

    # Не считаем ботов

    if message.from_user.is_bot:
        return

    # Только группы

    if message.chat.type not in (
        "group",
        "supergroup"
    ):
        return

    text = (
        message.text
        or
        message.caption
        or
        ""
    )

    # --------------------------------------------------------
    # Команды не считаем
    # --------------------------------------------------------

    if text.lstrip().startswith("/"):
        return

    words = count_words(
        text
    )

    characters = len(
        text
    )

    # Час по Москве

    message_time = (
        message.date.astimezone(
            MOSCOW
        )
    )

    save_message(

        chat_id=message.chat.id,

        user_id=(
            message.from_user.id
        ),

        user_name=(
            message.from_user.full_name
        ),

        words=words,

        characters=characters,

        hour=message_time.hour
    )


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

            text = make_today_stats_text(
                chat_id
            )

            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML
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
        .build()
    )

    # ========================================================
    # КОМАНДЫ
    # ========================================================

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

    # --------------------------------------------------------
    # INLINE-КНОПКИ /STATS
    # --------------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            stats_callback,
            pattern=r"^stats:"
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

    # ========================================================
    # СООБЩЕНИЯ
    # ========================================================

    app.add_handler(

        MessageHandler(

            (
                filters.TEXT
                |
                filters.CAPTION
            )
            &
            ~filters.COMMAND,

            count_message
        )
    )

    # ========================================================
    # ОТЧЁТ В 20:00 ПО МОСКВЕ
    # ========================================================

    app.job_queue.run_daily(

        daily_report,

        time=time(
            hour=20,
            minute=0,
            tzinfo=MOSCOW
        )
    )

    # ========================================================
    # ЗАПУСК
    # ========================================================

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
        "Ежедневный отчёт: 20:00"
    )

    print(
        "Интерактивный /stats: ВКЛ"
    )

    print(
        "Inline-кнопки: ВКЛ"
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

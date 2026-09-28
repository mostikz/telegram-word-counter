import os
import re
import sqlite3
from datetime import datetime, time, timezone, timedelta, date

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
    # Дневная статистика
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
    # Статистика по часам
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

        user_id = row[0]
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
        f"📅 Напиздели за сегодня\n\n"
        f"{pretty_date(today)}\n\n"
    )

    if not rows:

        return (
            result
            +
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
        f"{format_number(total_messages)}"
        f"\n"
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
            result
            +
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
        f"{format_number(users)}"
        f"\n"
        f"💬 Сообщений: "
        f"{format_number(messages)}"
        f"\n"
        f"📝 Слов: "
        f"{format_number(words)}"
    )

    return result



# ============================================================
# ИНТЕРФЕЙС БОТА — INLINE-КНОПКИ
# ============================================================

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import CallbackQueryHandler


def main_menu_keyboard():
    """Главное меню бота."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📅 Сегодня", callback_data="today"),
            InlineKeyboardButton("👤 Моя статистика", callback_data="me"),
        ],
        [
            InlineKeyboardButton("🏆 7 дней", callback_data="week"),
            InlineKeyboardButton("🗓 Месяц", callback_data="month"),
        ],
        [
            InlineKeyboardButton("📊 Группа", callback_data="group"),
            InlineKeyboardButton("📈 Активность", callback_data="activity"),
        ],
        [
            InlineKeyboardButton("🕐 По часам", callback_data="hours"),
            InlineKeyboardButton("🏅 Достижения", callback_data="achievements"),
        ],
        [
            InlineKeyboardButton("🏆 Рекорды", callback_data="records"),
            InlineKeyboardButton("🎲 Факт", callback_data="fact"),
        ],
        [
            InlineKeyboardButton("ℹ️ Помощь", callback_data="help"),
        ],
    ])


def back_keyboard():
    """Кнопка возврата в главное меню."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Главное меню", callback_data="menu")]
    ])


def screen_text():
    return (
        "🤖 Статистика чата\n\n"
        "Я считаю, кто сколько написал в группе.\n\n"
        "Выбери нужный раздел кнопкой ниже:"
    )


def group_required_text():
    return (
        "⚠️ Этот раздел работает только в группе.\n\n"
        "Добавь бота в группу и открой меню там."
    )


async def send_menu(update: Update):
    """Показывает главное меню обычным сообщением."""
    if update.message:
        await update.message.reply_text(
            screen_text(),
            reply_markup=main_menu_keyboard(),
        )


async def edit_screen(query, text, keyboard=None):
    """Обновляет текущее сообщение, не создавая новое."""
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard or back_keyboard(),
    )


def callback_group_only(query):
    """Проверка, что кнопка нажата внутри группы."""
    message = query.message
    return (
        message
        and message.chat
        and message.chat.type in ("group", "supergroup")
    )


def make_me_stats_text(chat_id, user):
    today = moscow_now().date()

    start_7, _ = last_n_days(7)

    start_month, end_month = current_month_range()

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
        today,
    )

    rank = get_rank(
        chat_id,
        user.id,
        today,
        today,
    )

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
        today,
    )

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
        end_month,
    )

    current_streak, best_streak = get_streaks(
        chat_id,
        user.id,
    )

    night = get_night_messages(
        chat_id,
        user.id,
    )

    avg = words / messages if messages else 0

    return (
        f"👤 {user.full_name}\n\n"

        f"📅 Сегодня:\n"
        f"📝 {format_number(words)} слов\n"
        f"💬 {format_number(messages)} сообщений\n"
        f"📊 {avg:.1f} слов/сообщение\n"
        f"🏆 Место: {rank if rank else '—'}\n\n"

        f"📆 За 7 дней: "
        f"{format_number(week_words)} слов / "
        f"{format_number(week_messages)} сообщ.\n"

        f"🗓 За месяц: "
        f"{format_number(month_words)} слов / "
        f"{format_number(month_messages)} сообщ.\n\n"

        f"🔥 Серия сейчас: {current_streak} дн.\n"
        f"🏅 Лучшая серия: {best_streak} дн.\n"
        f"🌙 Ночных сообщений: {format_number(night)}\n"
        f"📜 Самое длинное сегодня: "
        f"{format_number(longest_words)} слов"
    )


def make_group_stats_text(chat_id):
    start_date = date(2000, 1, 1)
    end_date = moscow_now().date()

    messages, words, users = get_group_totals(
        chat_id,
        start_date,
        end_date,
    )

    top = get_stats(
        chat_id,
        start_date,
        end_date,
    )[:1]

    text = (
        "📊 Наша группа\n\n"
        f"👥 Активных участников: {format_number(users)}\n"
        f"💬 Сообщений за всё время: {format_number(messages)}\n"
        f"📝 Слов за всё время: {format_number(words)}\n"
    )

    if top:
        text += (
            "\n"
            "👑 Главный болтун:\n"
            f"{top[0][1]} — "
            f"{format_number(top[0][3])} слов"
        )

    return text


def make_activity_text(chat_id):
    start_date, end_date = last_n_days(7)

    rows = get_daily_group_activity(
        chat_id,
        start_date,
        end_date,
    )

    by_date = {
        row[0]: (row[1], row[2])
        for row in rows
    }

    max_words = max(
        (value[1] for value in by_date.values()),
        default=0,
    )

    lines = [
        "📈 Активность группы за 7 дней",
        "",
    ]

    for i in range(7):
        d = start_date + timedelta(days=i)

        messages, words = by_date.get(
            date_str(d),
            (0, 0),
        )

        lines.append(
            f"{d.strftime('%a %d.%m')}: "
            f"{bar(words, max_words)} "
            f"{format_number(words)} слов"
        )

    return "\n".join(lines)


def make_hours_text(chat_id):
    start_date, end_date = last_n_days(7)

    rows = get_hourly_activity(
        chat_id,
        start_date,
        end_date,
    )

    by_hour = {
        row[0]: (row[1], row[2])
        for row in rows
    }

    max_words = max(
        (value[1] for value in by_hour.values()),
        default=0,
    )

    lines = [
        "🕐 Активность по часам за 7 дней",
        "",
    ]

    for hour in range(24):
        messages, words = by_hour.get(
            hour,
            (0, 0),
        )

        lines.append(
            f"{hour:02d}:00 "
            f"{bar(words, max_words, 12)} "
            f"{format_number(words)}"
        )

    return "\n".join(lines)


def make_achievements_text(chat_id, user):
    start_date = date(2000, 1, 1)
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
        end_date,
    )

    current_streak, best_streak = get_streaks(
        chat_id,
        user.id,
    )

    night = get_night_messages(
        chat_id,
        user.id,
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
            "💬 Не замолкает — 500 сообщений"
        )

    if messages >= 1_000:
        achievements_list.append(
            "⚡ Марафонец — 1 000 сообщений"
        )

    if longest_words >= 1_000:
        achievements_list.append(
            "📜 Простыня — сообщение на 1 000+ слов"
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
        return (
            "🏅 Достижения\n\n"
            "Пока достижений нет. "
            "Начинай напиздеть 😄"
        )

    return (
        "🏅 Твои достижения\n\n"
        + "\n".join(achievements_list)
    )


def make_records_text(chat_id):
    (
        best_words,
        best_messages,
        longest_message
    ) = get_best_records(chat_id)

    lines = [
        "🏆 Рекорды группы",
        "",
    ]

    if best_words:
        lines.append(
            "📝 Больше всего слов за день:\n"
            f"{best_words[0]} — "
            f"{format_number(best_words[1])} слов "
            f"({best_words[2]})"
        )

    if best_messages:
        lines.append(
            "\n💬 Больше всего сообщений за день:\n"
            f"{best_messages[0]} — "
            f"{format_number(best_messages[1])} сообщений "
            f"({best_messages[2]})"
        )

    if longest_message:
        lines.append(
            "\n📜 Самое длинное сообщение:\n"
            f"{longest_message[0]} — "
            f"{format_number(longest_message[1])} слов / "
            f"{format_number(longest_message[2])} символов "
            f"({longest_message[3]})"
        )

    if len(lines) == 2:
        lines.append("Рекордов пока нет 😄")

    return "\n".join(lines)


def make_fact_text(chat_id):
    import random

    start_date = date(2000, 1, 1)
    end_date = moscow_now().date()

    rows = get_stats(
        chat_id,
        start_date,
        end_date,
    )

    if not rows:
        return "🤔 Пока мало данных для фактов."

    facts = []

    top = rows[0]

    facts.append(
        f"👑 {top[1]} — главный болтун группы: "
        f"{format_number(top[3])} слов за всё время."
    )

    longest = max(
        rows,
        key=lambda row: row[5],
    )

    if longest[5]:
        facts.append(
            f"📜 {longest[1]} однажды написал(а) "
            f"{format_number(longest[5])} слов за день "
            f"в одном сообщении."
        )

    messages, words, users = get_group_totals(
        chat_id,
        start_date,
        end_date,
    )

    if users:
        facts.append(
            f"👥 В статистике группы уже есть "
            f"{format_number(users)} активных участников."
        )

    if messages:
        facts.append(
            f"💬 Всего группа отправила "
            f"{format_number(messages)} сообщений."
        )

    if words:
        facts.append(
            f"📝 Всего группа написала "
            f"{format_number(words)} слов."
        )

    return "🎲 Факт дня\n\n" + random.choice(facts)


# ============================================================
# /START — единственная команда для запуска меню
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await send_menu(update)


# ============================================================
# CALLBACK-КНОПКИ
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    action = query.data

    # Главное меню
    if action == "menu":
        await edit_screen(
            query,
            screen_text(),
            main_menu_keyboard(),
        )
        return

    # Помощь
    if action == "help":
        text = (
            "ℹ️ Как пользоваться ботом\n\n"
            "Просто нажимай кнопки в меню.\n\n"
            "📅 Сегодня — статистика за текущий день.\n"
            "👤 Моя статистика — твои личные показатели.\n"
            "🏆 7 дней — рейтинг за последние 7 дней.\n"
            "🗓 Месяц — рейтинг за текущий месяц.\n"
            "📊 Группа — статистика за всё время.\n"
            "📈 Активность — активность по дням.\n"
            "🕐 По часам — активность по часам.\n"
            "🏅 Достижения — твои достижения.\n"
            "🏆 Рекорды — рекорды группы.\n"
            "🎲 Факт — случайный факт о группе."
        )

        await edit_screen(
            query,
            text,
        )
        return

    # Все остальные разделы работают только в группе.
    if not callback_group_only(query):
        await edit_screen(
            query,
            group_required_text(),
        )
        return

    chat_id = query.message.chat.id

    if action == "today":
        await edit_screen(
            query,
            make_today_stats_text(chat_id),
        )
        return

    if action == "me":
        user = query.from_user

        await edit_screen(
            query,
            make_me_stats_text(
                chat_id,
                user,
            ),
        )
        return

    if action == "week":
        start_date, end_date = last_n_days(7)

        await edit_screen(
            query,
            make_period_stats_text(
                chat_id,
                "🏆 Топ за 7 дней",
                start_date,
                end_date,
            ),
        )
        return

    if action == "month":
        start_date, end_date = current_month_range()

        await edit_screen(
            query,
            make_period_stats_text(
                chat_id,
                "🏆 Топ за месяц",
                start_date,
                end_date,
            ),
        )
        return

    if action == "group":
        await edit_screen(
            query,
            make_group_stats_text(chat_id),
        )
        return

    if action == "activity":
        await edit_screen(
            query,
            make_activity_text(chat_id),
        )
        return

    if action == "hours":
        await edit_screen(
            query,
            make_hours_text(chat_id),
        )
        return

    if action == "achievements":
        await edit_screen(
            query,
            make_achievements_text(
                chat_id,
                query.from_user,
            ),
        )
        return

    if action == "records":
        await edit_screen(
            query,
            make_records_text(chat_id),
        )
        return

    if action == "fact":
        await edit_screen(
            query,
            make_fact_text(chat_id),
        )
        return


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

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    # /start оставлен только как первоначальная точка входа.
    # После открытия меню всё управление идёт кнопками.
    app.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    # --------------------------------------------------------
    # INLINE-КНОПКИ
    # --------------------------------------------------------

    app.add_handler(
        CallbackQueryHandler(
            button_handler,
        )
    )

    # --------------------------------------------------------
    # СООБЩЕНИЯ
    # --------------------------------------------------------

    app.add_handler(
        MessageHandler(
            (
                filters.TEXT
                |
                filters.CAPTION
            )
            &
            ~filters.COMMAND,
            count_message,
        )
    )

    # --------------------------------------------------------
    # ОТЧЁТ В 20:00 ПО МОСКВЕ
    # --------------------------------------------------------

    app.job_queue.run_daily(
        daily_report,
        time=time(
            hour=20,
            minute=0,
            tzinfo=MOSCOW,
        ),
    )

    print("================================")
    print("Бот запущен!")
    print("Часовой пояс: Москва (UTC+3)")
    print("Ежедневный отчёт: 20:00")
    print("Управление: INLINE-КНОПКИ")
    print("================================")

    app.run_polling()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    main()
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
    # Команды не считаем как обычные сообщения.
    # --------------------------------------------------------

    if text.lstrip().startswith("/"):
        return

    words = count_words(text)

    characters = len(text)

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

            # ВАЖНО:
            # здесь остаётся именно статистика
            # ТЕКУЩЕГО дня на момент 20:00.

            text = make_today_stats_text(
                chat_id
            )

            await context.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=main_menu_keyboard(),
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


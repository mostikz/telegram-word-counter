import os
import re
import sqlite3
from datetime import datetime, time, timezone, timedelta, date

from telegram import (
    Update,
    BotCommand,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ChatMemberHandler,
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

    # One pinned menu message per chat.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_menu_messages (
            chat_id INTEGER PRIMARY KEY,
            message_id INTEGER NOT NULL
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
# /START
# ============================================================

# ============================================================
# ПОСТОЯННАЯ КЛАВИАТУРА ВНИЗУ ЧАТА
# ============================================================

MENU_TODAY = "📅 Сегодня"
MENU_ME = "👤 Моя статистика"
MENU_WEEK = "🏆 7 дней"
MENU_MONTH = "🗓 Месяц"
MENU_GROUP = "📊 Группа"
MENU_ACTIVITY = "📈 Активность"
MENU_HOURS = "🕐 По часам"
MENU_ACHIEVEMENTS = "🏅 Достижения"
MENU_RECORDS = "🏆 Рекорды"
MENU_FACT = "🎲 Факт"
MENU_HELP = "ℹ️ Помощь"


def reply_keyboard():
    """
    Это именно Reply Keyboard Telegram.
    Она появляется над полем ввода после отправки ботом
    сообщения с reply_markup=ReplyKeyboardMarkup(...).
    """
    return ReplyKeyboardMarkup(
        [
            ["📅 Сегодня", "👤 Моя статистика"],
            ["🏆 7 дней", "🗓 Месяц"],
            ["📊 Группа", "📈 Активность"],
            ["🕐 По часам", "🏅 Достижения"],
            ["🏆 Рекорды", "🎲 Факт"],
            ["ℹ️ Помощь"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Выбери раздел 👇",
    )

async def get_saved_menu_message_id(chat_id: int):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        "SELECT message_id FROM bot_menu_messages WHERE chat_id = ?",
        (chat_id,),
    )
    row = cursor.fetchone()
    db.close()
    return int(row[0]) if row else None


async def save_menu_message_id(chat_id: int, message_id: int):
    db = get_db()
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO bot_menu_messages(chat_id, message_id)
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            message_id = excluded.message_id
        """,
        (chat_id, message_id),
    )
    db.commit()
    db.close()


async def remove_saved_menu(chat_id: int, bot):
    old_message_id = await get_saved_menu_message_id(chat_id)
    if not old_message_id:
        return

    try:
        await bot.unpin_chat_message(
            chat_id=chat_id,
            message_id=old_message_id,
        )
    except Exception:
        pass

    try:
        await bot.delete_message(
            chat_id=chat_id,
            message_id=old_message_id,
        )
    except Exception:
        pass


async def send_group_menu(
    bot,
    chat_id: int,
    text: str = "🤖 МЕНЮ БОТА",
    replace_existing: bool = False,
):
    """
    Создаёт ровно одно служебное меню в группе и закрепляет его.

    Само Reply Keyboard находится над полем ввода.
    В чате остаётся только одно сообщение-меню, закреплённое сверху.
    """
    old_message_id = await get_saved_menu_message_id(chat_id)

    if old_message_id and not replace_existing:
        # Существующее меню уже создано. Reply Keyboard в Telegram
        # остаётся активной, поэтому второе сообщение не создаём.
        return old_message_id

    if replace_existing:
        await remove_saved_menu(chat_id, bot)

    menu_message = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_keyboard(),
        disable_notification=True,
    )

    await save_menu_message_id(chat_id, menu_message.message_id)

    try:
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=menu_message.message_id,
            disable_notification=True,
        )
        print(
            f"Меню закреплено в чате {chat_id}, "
            f"message_id={menu_message.message_id}"
        )
    except Exception as error:
        print(
            f"Не удалось закрепить меню в чате {chat_id}: {error}. "
            "Проверь права бота на закрепление сообщений."
        )

    return menu_message.message_id



async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return

    if message.chat.type in ("group", "supergroup"):
        await send_group_menu(
            context.bot,
            message.chat.id,
            "🤖 МЕНЮ БОТА\n\n"
            "Меню находится над строкой ввода 👇",
            replace_existing=True,
        )
        try:
            await message.delete()
        except Exception:
            pass
    else:
        await message.reply_text(
            "Меню бота 👇",
            reply_markup=reply_keyboard(),
        )



async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message
    if not message:
        return

    if message.chat.type in ("group", "supergroup"):
        await send_group_menu(
            context.bot,
            message.chat.id,
            "🤖 МЕНЮ БОТА\n\n"
            "Выбирай раздел кнопками над строкой ввода 👇",
        )
        try:
            await message.delete()
        except Exception:
            pass
        return

    await message.reply_text(
        "Привет! 🤖\n\n"
        "Это постоянное меню бота.\n"
        "Кнопки находятся прямо над строкой ввода 👇",
        reply_markup=reply_keyboard(),
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

    # Сегодня

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

    # Неделя

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

    # Месяц

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

    # Серии

    current_streak, best_streak = (
        get_streaks(
            chat_id,
            user.id
        )
    )

    # Ночь

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

    start_date, end_date = (
        last_n_days(7)
    )

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

    start_date, end_date = (
        last_n_days(7)
    )

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

    # --------------------------------------------------------
    # Слова
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Сообщения
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Длинные сообщения
    # --------------------------------------------------------

    if longest_words >= 1_000:

        achievements_list.append(
            "📜 Простыня — "
            "сообщение на 1 000+ слов"
        )

    # --------------------------------------------------------
    # Ночь
    # --------------------------------------------------------

    if night >= 100:

        achievements_list.append(
            "🌙 Ночной житель — "
            "100 ночных сообщений"
        )

    # --------------------------------------------------------
    # Серия
    # --------------------------------------------------------

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
    # Рекорд слов
    # --------------------------------------------------------

    if best_words:

        lines.append(

            "📝 Больше всего слов за день:\n"
            f"{best_words[0]} — "
            f"{format_number(best_words[1])} слов "
            f"({best_words[2]})"
        )

    # --------------------------------------------------------
    # Рекорд сообщений
    # --------------------------------------------------------

    if best_messages:

        lines.append(

            "\n💬 Больше всего сообщений за день:\n"
            f"{best_messages[0]} — "
            f"{format_number(best_messages[1])} сообщений "
            f"({best_messages[2]})"
        )

    # --------------------------------------------------------
    # Самое длинное сообщение
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

    import random

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
    # Главный болтун
    # --------------------------------------------------------

    top = rows[0]

    facts.append(

        f"👑 {top[1]} — "
        f"главный болтун группы: "
        f"{format_number(top[3])} "
        f"слов за всё время."
    )

    # --------------------------------------------------------
    # Самый длинный текст
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
    # Участники
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
    # Сообщения
    # --------------------------------------------------------

    if messages:

        facts.append(

            f"💬 Всего группа отправила "
            f"{format_number(messages)} "
            f"сообщений."
        )

    # --------------------------------------------------------
    # Слова
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


# Кнопка -> существующий обработчик.
MENU_HANDLERS = {
    MENU_TODAY: stats,
    MENU_ME: me,
    MENU_WEEK: week,
    MENU_MONTH: month,
    MENU_GROUP: group,
    MENU_ACTIVITY: activity,
    MENU_HOURS: hours,
    MENU_ACHIEVEMENTS: achievements,
    MENU_RECORDS: records,
    MENU_FACT: fact,
    MENU_HELP: help_command,
}



async def delete_menu_message_if_needed(message):
    """
    Нажатие Reply Keyboard приходит в группу как обычное сообщение.
    Удаляем техническое сообщение после обработки кнопки.
    Нужны права бота на удаление сообщений в группе.
    """
    if not message:
        return

    if message.chat.type not in ("group", "supergroup"):
        return

    try:
        await message.delete()
    except Exception as error:
        # Бот продолжит работать даже без права удаления.
        print(
            f"Не удалось удалить нажатие меню "
            f"в чате {message.chat.id}: {error}"
        )

# ============================================================
# ОБРАБОТКА НАЖАТИЙ ПОСТОЯННОГО МЕНЮ
# ============================================================

async def reply_keyboard_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """
    Обрабатывает нажатия Reply Keyboard.
    Кнопка приходит как обычный текст.
    """
    message = update.effective_message
    if not message:
        return

    text_value = (message.text or "").strip()

    if text_value in MENU_HANDLERS:
        await MENU_HANDLERS[text_value](update, context)

        return

    await count_message(update, context)




async def bot_added_to_group(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    При добавлении бота в группу создаём одно служебное меню,
    прикрепляем Reply Keyboard и закрепляем сообщение меню.
    """
    event = update.my_chat_member
    if not event:
        return

    chat = event.chat
    if chat.type not in ("group", "supergroup"):
        return

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    was_in_chat = old_status in ("member", "administrator")
    is_in_chat = new_status in ("member", "administrator")

    if is_in_chat and not was_in_chat:
        try:
            await send_group_menu(
                context.bot,
                chat.id,
                "🤖 Бот подключён\n\n"
                "Меню бота закреплено сверху.\n"
                "Кнопки доступны над строкой ввода 👇",
            )
        except Exception as error:
            print(
                f"Не удалось создать меню в группе "
                f"{chat.id}: {error}"
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

    # --------------------------------------------------------
    # ПЕРЕСЛАННЫЕ СООБЩЕНИЯ НЕ СЧИТАЕМ
    #
    # В современных версиях python-telegram-bot Telegram
    # передаёт источник пересылки через forward_origin.
    # Дополнительно проверяем старые поля для совместимости.
    # --------------------------------------------------------

    if getattr(message, "forward_origin", None) is not None:
        return

    if (
        getattr(message, "forward_from", None) is not None
        or
        getattr(message, "forward_from_chat", None) is not None
        or
        getattr(message, "forward_sender_name", None) is not None
    ):
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

            # В группе сначала убеждаемся, что существует единое
            # закреплённое меню. Сам отчёт клавиатуру не дублирует.
            await send_group_menu(
                context.bot,
                chat_id,
                "🤖 МЕНЮ БОТА\n\n"
                "Кнопки меню доступны над строкой ввода 👇",
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
    BotCommand("menu", "Показать меню"),
    BotCommand("start", "Открыть постоянное меню"),
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


async def post_init(application: Application):
    """
    Настраивает системное меню Telegram, а /start и /menu
    показывают именно Reply Keyboard над строкой ввода.

    Важно: системное Menu Telegram и Reply Keyboard — это
    два разных механизма. Reply Keyboard появляется только
    после отправки сообщения с reply_markup.
    """
    from telegram import BotCommandScopeDefault, MenuButtonCommands

    await application.bot.set_my_commands(
        BOT_COMMANDS,
        scope=BotCommandScopeDefault(),
    )

    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonCommands()
    )

    print("Системное Menu Telegram настроено.")
    print("Группы: одно закреплённое сообщение-меню + Reply Keyboard.")



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
        ChatMemberHandler(
            bot_added_to_group,
            ChatMemberHandler.MY_CHAT_MEMBER,
        )
    )

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

            (
                filters.TEXT
                |
                filters.CAPTION
            )
            &
            ~filters.COMMAND,

            reply_keyboard_handler
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
        "Ежедневный отчёт: 20:00"
    )

    print(
        "Постоянное меню Reply Keyboard: включено"
    )

    print(
        "Reply Keyboard: /start или /menu"
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

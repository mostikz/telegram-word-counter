import os
import random
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from functools import wraps

from telegram import (
    BotCommand,
    BotCommandScopeDefault,
    MenuButtonCommands,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ============================================================
# НАСТРОЙКИ
# ============================================================

# Токен берём из переменной окружения, в коде его не храним.
# Linux/macOS:  export BOT_TOKEN="токен"
# PowerShell:   $env:BOT_TOKEN="токен"
TOKEN = os.getenv("BOT_TOKEN", "")

# Москва = UTC+3
MOSCOW = timezone(timedelta(hours=3))

DATABASE = "stats.db"

# Время ежедневного отчёта (по Москве)
REPORT_HOUR = 20
REPORT_MINUTE = 0

GROUP_TYPES = ("group", "supergroup")
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
EPOCH = date(2000, 1, 1)


# ============================================================
# БАЗА ДАННЫХ
# ============================================================

def get_db():
    db = sqlite3.connect(DATABASE, timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    return db


def init_database():
    db = get_db()

    # Старые базы с дополнительными колонками (words, characters и т.д.)
    # продолжают работать: у этих колонок есть DEFAULT 0.
    db.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            chat_id   INTEGER NOT NULL,
            user_id   INTEGER NOT NULL,
            user_name TEXT    NOT NULL,
            date      TEXT    NOT NULL,
            messages  INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id, date)
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS hourly_stats (
            chat_id  INTEGER NOT NULL,
            user_id  INTEGER NOT NULL,
            date     TEXT    NOT NULL,
            hour     INTEGER NOT NULL,
            messages INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, user_id, date, hour)
        )
    """)

    # Последнее сообщение-меню в каждом чате (чтобы не плодить дубли)
    db.execute("""
        CREATE TABLE IF NOT EXISTS bot_menu_messages (
            chat_id    INTEGER PRIMARY KEY,
            message_id INTEGER NOT NULL
        )
    """)

    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_daily_chat_date "
        "ON daily_stats(chat_id, date)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_hourly_chat_date "
        "ON hourly_stats(chat_id, date)"
    )

    db.commit()
    db.close()


# ============================================================
# ВРЕМЯ
# ============================================================

def moscow_now():
    return datetime.now(MOSCOW)


def date_str(value):
    return value.strftime("%Y-%m-%d")


def pretty_date(value):
    return value.strftime("%d.%m.%Y")


def last_n_days(n):
    """Последние n дней, включая сегодняшний."""
    today = moscow_now().date()
    return today - timedelta(days=n - 1), today


def current_month_range():
    today = moscow_now().date()
    return today.replace(day=1), today


# ============================================================
# ЗАПИСЬ СООБЩЕНИЙ
# ============================================================

def save_message(chat_id, user_id, user_name, message_time):
    day = date_str(message_time)
    hour = message_time.hour

    db = get_db()

    db.execute("""
        INSERT INTO daily_stats (chat_id, user_id, user_name, date, messages)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(chat_id, user_id, date) DO UPDATE SET
            user_name = excluded.user_name,
            messages  = messages + 1
    """, (chat_id, user_id, user_name, day))

    db.execute("""
        INSERT INTO hourly_stats (chat_id, user_id, date, hour, messages)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(chat_id, user_id, date, hour) DO UPDATE SET
            messages = messages + 1
    """, (chat_id, user_id, day, hour))

    db.commit()
    db.close()


# ============================================================
# ПОЛУЧЕНИЕ СТАТИСТИКИ
# ============================================================

def get_stats(chat_id, start_date, end_date):
    """[(user_id, имя, сообщений)] по убыванию активности."""
    db = get_db()
    rows = db.execute("""
        SELECT
            d.user_id,
            (
                SELECT n.user_name
                FROM daily_stats n
                WHERE n.chat_id = d.chat_id AND n.user_id = d.user_id
                ORDER BY n.date DESC
                LIMIT 1
            ) AS name,
            SUM(d.messages) AS total
        FROM daily_stats d
        WHERE d.chat_id = ?
          AND d.date BETWEEN ? AND ?
        GROUP BY d.user_id
        ORDER BY total DESC, d.user_id ASC
    """, (chat_id, date_str(start_date), date_str(end_date))).fetchall()
    db.close()
    return rows


def get_user_messages(chat_id, user_id, start_date, end_date):
    db = get_db()
    value = db.execute("""
        SELECT COALESCE(SUM(messages), 0)
        FROM daily_stats
        WHERE chat_id = ? AND user_id = ?
          AND date BETWEEN ? AND ?
    """, (chat_id, user_id, date_str(start_date), date_str(end_date))
    ).fetchone()[0]
    db.close()
    return value


def get_rank(chat_id, user_id, start_date, end_date):
    for position, row in enumerate(
        get_stats(chat_id, start_date, end_date), start=1
    ):
        if row[0] == user_id:
            return position
    return None


def get_group_totals(chat_id, start_date, end_date):
    """(всего сообщений, активных участников)"""
    db = get_db()
    messages, users = db.execute("""
        SELECT COALESCE(SUM(messages), 0), COUNT(DISTINCT user_id)
        FROM daily_stats
        WHERE chat_id = ? AND date BETWEEN ? AND ?
    """, (chat_id, date_str(start_date), date_str(end_date))).fetchone()
    db.close()
    return messages, users


def get_daily_group_activity(chat_id, start_date, end_date):
    db = get_db()
    rows = db.execute("""
        SELECT date, SUM(messages)
        FROM daily_stats
        WHERE chat_id = ? AND date BETWEEN ? AND ?
        GROUP BY date
        ORDER BY date
    """, (chat_id, date_str(start_date), date_str(end_date))).fetchall()
    db.close()
    return rows


def get_hourly_activity(chat_id, start_date, end_date):
    db = get_db()
    rows = db.execute("""
        SELECT hour, SUM(messages)
        FROM hourly_stats
        WHERE chat_id = ? AND date BETWEEN ? AND ?
        GROUP BY hour
        ORDER BY hour
    """, (chat_id, date_str(start_date), date_str(end_date))).fetchall()
    db.close()
    return rows


def get_night_messages(chat_id, user_id):
    """Сообщения с 00:00 до 05:00 за всё время."""
    db = get_db()
    value = db.execute("""
        SELECT COALESCE(SUM(messages), 0)
        FROM hourly_stats
        WHERE chat_id = ? AND user_id = ?
          AND hour >= 0 AND hour < 5
    """, (chat_id, user_id)).fetchone()[0]
    db.close()
    return value


def get_best_day(chat_id):
    """(имя, сообщений, дата) — лучший день одного участника."""
    db = get_db()
    row = db.execute("""
        SELECT user_name, messages, date
        FROM daily_stats
        WHERE chat_id = ?
        ORDER BY messages DESC, date ASC
        LIMIT 1
    """, (chat_id,)).fetchone()
    db.close()
    return row


def get_user_dates(chat_id, user_id):
    db = get_db()
    rows = db.execute("""
        SELECT DISTINCT date
        FROM daily_stats
        WHERE chat_id = ? AND user_id = ?
    """, (chat_id, user_id)).fetchall()
    db.close()
    return {date.fromisoformat(row[0]) for row in rows}


def get_streaks(chat_id, user_id):
    """(текущая серия, лучшая серия) в днях."""
    dates = get_user_dates(chat_id, user_id)
    if not dates:
        return 0, 0

    # Текущая серия. Если сегодня ещё не писал, серия не сгорает
    # и считается от вчерашнего дня.
    day = moscow_now().date()
    if day not in dates:
        day -= timedelta(days=1)

    current = 0
    while day in dates:
        current += 1
        day -= timedelta(days=1)

    # Лучшая серия
    best = 0
    length = 0
    previous = None
    for d in sorted(dates):
        if previous is not None and d - previous == timedelta(days=1):
            length += 1
        else:
            length = 1
        best = max(best, length)
        previous = d

    return current, best


# ============================================================
# ФОРМАТИРОВАНИЕ
# ============================================================

def medal(position):
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(position, f"{position}.")


def format_number(value):
    return f"{int(value):,}".replace(",", " ")


def format_period(start_date, end_date):
    return f"{pretty_date(start_date)} — {pretty_date(end_date)}"


def bar(value, max_value, width=15):
    if max_value <= 0 or not value:
        return "░" * width
    filled = max(1, round(value / max_value * width))
    return "█" * filled + "░" * (width - filled)


def stats_lines(rows):
    return [
        f"{medal(position)} {row[1]} — {format_number(row[2])} сообщений"
        for position, row in enumerate(rows, start=1)
    ]


def make_today_stats_text(chat_id):
    today = moscow_now().date()
    rows = get_stats(chat_id, today, today)

    result = f"📅 Сообщения за сегодня\n\n{pretty_date(today)}\n\n"

    if not rows:
        return result + "Пока сообщений нет 😄"

    total, _ = get_group_totals(chat_id, today, today)

    return (
        result
        + "\n".join(stats_lines(rows))
        + f"\n\n💬 Всего сообщений: {format_number(total)}"
    )


def make_period_stats_text(chat_id, title, start_date, end_date):
    rows = get_stats(chat_id, start_date, end_date)

    result = (
        f"{title}\n\n"
        f"📅 {format_period(start_date, end_date)}\n\n"
    )

    if not rows:
        return result + "Пока статистики нет 😄"

    total, users = get_group_totals(chat_id, start_date, end_date)

    return (
        result
        + "\n".join(stats_lines(rows))
        + f"\n\n👥 Активных участников: {format_number(users)}"
        + f"\n💬 Всего сообщений: {format_number(total)}"
    )


# ============================================================
# МЕНЮ (Reply Keyboard)
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


def reply_keyboard():
    """
    one_time_keyboard=True: после нажатия кнопки клавиатура сворачивается,
    а справа в строке ввода остаётся значок клавиатуры, по которому её
    можно открыть снова. Сама по себе клавиатура при входе в чат
    не разворачивается.
    """
    return ReplyKeyboardMarkup(
        [
            [MENU_TODAY, MENU_ME],
            [MENU_WEEK, MENU_MONTH],
            [MENU_GROUP, MENU_ACTIVITY],
            [MENU_HOURS, MENU_ACHIEVEMENTS],
            [MENU_RECORDS, MENU_FACT],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        is_persistent=False,
    )


def get_saved_menu_message_id(chat_id):
    db = get_db()
    row = db.execute(
        "SELECT message_id FROM bot_menu_messages WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    db.close()
    return int(row[0]) if row else None


def save_menu_message_id(chat_id, message_id):
    db = get_db()
    db.execute("""
        INSERT INTO bot_menu_messages (chat_id, message_id)
        VALUES (?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET message_id = excluded.message_id
    """, (chat_id, message_id))
    db.commit()
    db.close()


async def send_menu(bot, chat_id, text="🤖 Меню бота"):
    """
    Отправляет сообщение с клавиатурой. Предыдущее сообщение-меню
    удаляется, чтобы в чате не копились дубли.
    """
    old_message_id = get_saved_menu_message_id(chat_id)
    if old_message_id:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=old_message_id)
        except Exception:
            pass

    menu_message = await bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_keyboard(),
        disable_notification=True,
    )
    save_menu_message_id(chat_id, menu_message.message_id)


async def try_delete(message):
    """Удаляет сообщение в группе (нужно право бота на удаление)."""
    if message and message.chat.type in GROUP_TYPES:
        try:
            await message.delete()
        except Exception as error:
            print(f"Не удалось удалить сообщение в чате {message.chat.id}: {error}")


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/menu и /start"""
    message = update.effective_message
    if not message:
        return

    if message.chat.type in GROUP_TYPES:
        await send_menu(
            context.bot,
            message.chat.id,
            "🤖 Меню бота\n\nКнопки открываются значком клавиатуры "
            "справа в строке ввода 👇",
        )
        await try_delete(message)
    else:
        await message.reply_text(
            "Привет! 🤖\n\nЭто бот со статистикой сообщений группы.\n"
            "Добавь его в группу и открой меню командой /menu.",
            reply_markup=reply_keyboard(),
        )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message:
        return

    await message.reply_text(
        "ℹ️ Команды бота\n\n"
        "/menu — показать меню\n"
        "/stats — сообщения за сегодня\n"
        "/me — моя статистика\n"
        "/week — топ за 7 дней\n"
        "/month — топ за месяц\n"
        "/group — статистика группы\n"
        "/activity — активность по дням\n"
        "/hours — активность по часам\n"
        "/achievements — достижения\n"
        "/records — рекорды\n"
        "/fact — случайный факт"
    )
    await try_delete(message)


# ============================================================
# КОМАНДЫ СТАТИСТИКИ
# ============================================================

def group_only(handler):
    """Команда работает только в группах."""

    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        chat = update.effective_chat
        if not message or not chat:
            return

        if chat.type not in GROUP_TYPES:
            await message.reply_text("Эту команду нужно использовать в группе.")
            return

        await handler(update, context)

    return wrapper


@group_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        make_today_stats_text(update.effective_chat.id)
    )


@group_only
async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    today = moscow_now().date()
    start_7, _ = last_n_days(7)
    start_month, end_month = current_month_range()

    today_messages = get_user_messages(chat_id, user.id, today, today)
    week_messages = get_user_messages(chat_id, user.id, start_7, today)
    month_messages = get_user_messages(chat_id, user.id, start_month, end_month)

    rank = get_rank(chat_id, user.id, today, today)
    current_streak, best_streak = get_streaks(chat_id, user.id)
    night = get_night_messages(chat_id, user.id)

    await update.effective_message.reply_text(
        f"👤 {user.full_name}\n\n"
        f"📅 Сегодня: {format_number(today_messages)} сообщений\n"
        f"🏆 Место сегодня: {rank if rank else '—'}\n\n"
        f"📆 За 7 дней: {format_number(week_messages)} сообщений\n"
        f"🗓 За месяц: {format_number(month_messages)} сообщений\n\n"
        f"🔥 Серия сейчас: {current_streak} дн.\n"
        f"🏅 Лучшая серия: {best_streak} дн.\n"
        f"🌙 Ночных сообщений: {format_number(night)}"
    )


@group_only
async def week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_date, end_date = last_n_days(7)
    await update.effective_message.reply_text(
        make_period_stats_text(
            update.effective_chat.id, "🏆 Топ за 7 дней", start_date, end_date
        )
    )


@group_only
async def month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_date, end_date = current_month_range()
    await update.effective_message.reply_text(
        make_period_stats_text(
            update.effective_chat.id, "🏆 Топ за месяц", start_date, end_date
        )
    )


@group_only
async def group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    end_date = moscow_now().date()

    messages, users = get_group_totals(chat_id, EPOCH, end_date)
    top = get_stats(chat_id, EPOCH, end_date)[:1]

    text = (
        "📊 Наша группа\n\n"
        f"👥 Активных участников: {format_number(users)}\n"
        f"💬 Сообщений за всё время: {format_number(messages)}"
    )

    if top:
        text += (
            "\n\n👑 Самый активный участник:\n"
            f"{top[0][1]} — {format_number(top[0][2])} сообщений"
        )

    await update.effective_message.reply_text(text)


@group_only
async def activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    start_date, end_date = last_n_days(7)

    by_date = dict(get_daily_group_activity(chat_id, start_date, end_date))
    max_messages = max(by_date.values(), default=0)

    lines = ["📈 Активность группы за 7 дней", ""]

    for i in range(7):
        d = start_date + timedelta(days=i)
        messages = by_date.get(date_str(d), 0)
        lines.append(
            f"{WEEKDAYS[d.weekday()]} {d.strftime('%d.%m')}: "
            f"{bar(messages, max_messages)} {format_number(messages)}"
        )

    await update.effective_message.reply_text("\n".join(lines))


@group_only
async def hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    start_date, end_date = last_n_days(7)

    by_hour = dict(get_hourly_activity(chat_id, start_date, end_date))
    max_messages = max(by_hour.values(), default=0)

    lines = ["🕐 Активность по часам за 7 дней", ""]

    for hour in range(24):
        messages = by_hour.get(hour, 0)
        lines.append(
            f"{hour:02d}:00 {bar(messages, max_messages, 12)} "
            f"{format_number(messages)}"
        )

    await update.effective_message.reply_text("\n".join(lines))


@group_only
async def achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    today = moscow_now().date()

    messages = get_user_messages(chat_id, user.id, EPOCH, today)
    _, best_streak = get_streaks(chat_id, user.id)
    night = get_night_messages(chat_id, user.id)

    earned = []

    if messages >= 100:
        earned.append("💬 Разговорился — 100 сообщений")
    if messages >= 500:
        earned.append("🔥 Не замолкает — 500 сообщений")
    if messages >= 1000:
        earned.append("⚡ Марафонец — 1 000 сообщений")
    if messages >= 5000:
        earned.append("🚀 Турборежим — 5 000 сообщений")
    if night >= 100:
        earned.append("🌙 Ночной житель — 100 ночных сообщений")
    if best_streak >= 7:
        earned.append("🔥 Серия — 7 дней подряд")
    if best_streak >= 30:
        earned.append("🔥🔥 Месяц без молчания — 30 дней подряд")

    if earned:
        text = "🏅 Твои достижения\n\n" + "\n".join(earned)
    else:
        text = "🏅 Достижения\n\nПока достижений нет. Отправляй сообщения 😄"

    await update.effective_message.reply_text(text)


@group_only
async def records(update: Update, context: ContextTypes.DEFAULT_TYPE):
    best = get_best_day(update.effective_chat.id)

    lines = ["🏆 Рекорд группы", ""]

    if best:
        lines.append("💬 Больше всего сообщений за день:")
        lines.append(f"{best[0]} — {format_number(best[1])} сообщений ({best[2]})")
    else:
        lines.append("Рекордов пока нет 😄")

    await update.effective_message.reply_text("\n".join(lines))


@group_only
async def fact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    end_date = moscow_now().date()

    rows = get_stats(chat_id, EPOCH, end_date)

    if not rows:
        await update.effective_message.reply_text("🤔 Пока мало данных для фактов.")
        return

    messages, users = get_group_totals(chat_id, EPOCH, end_date)
    top = rows[0]

    facts = [
        f"👑 {top[1]} — самый активный участник: "
        f"{format_number(top[2])} сообщений за всё время.",
        f"👥 В статистике группы уже {format_number(users)} активных участников.",
        f"💬 Всего группа отправила {format_number(messages)} сообщений.",
    ]

    await update.effective_message.reply_text(
        "🎲 Факт дня\n\n" + random.choice(facts)
    )


# Кнопка меню -> обработчик
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
}


# ============================================================
# ОБРАБОТКА СООБЩЕНИЙ
# ============================================================

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Нажатие кнопки меню приходит как обычный текст."""
    message = update.effective_message
    if not message:
        return

    text_value = (message.text or "").strip()

    if text_value in MENU_HANDLERS:
        await MENU_HANDLERS[text_value](update, context)
        # Убираем «служебное» сообщение с названием кнопки
        await try_delete(message)
        return

    await count_message(update, context)


CONTENT_FIELDS = (
    "text", "caption", "audio", "document", "animation", "game", "photo",
    "sticker", "video", "voice", "video_note", "contact", "location",
    "venue", "poll", "dice", "story", "paid_media", "checklist",
)


async def count_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message

    if not message or message.chat.type not in GROUP_TYPES:
        return

    if not message.from_user or message.from_user.is_bot:
        return

    # Пересылки не считаются
    if getattr(message, "forward_origin", None) is not None:
        return
    if getattr(message, "is_automatic_forward", False):
        return
    if (
        getattr(message, "forward_from", None) is not None
        or getattr(message, "forward_from_chat", None) is not None
        or getattr(message, "forward_sender_name", None) is not None
    ):
        return

    # Команды не считаются
    if message.text and message.text.lstrip().startswith("/"):
        return

    # Служебные сообщения (вход/выход, закрепление и т.д.) не считаются
    if not any(getattr(message, f, None) is not None for f in CONTENT_FIELDS):
        return

    save_message(
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        user_name=message.from_user.full_name,
        message_time=message.date.astimezone(MOSCOW),
    )


async def bot_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Когда бота добавляют в группу, один раз выдаём меню."""
    event = update.my_chat_member
    if not event or event.chat.type not in GROUP_TYPES:
        return

    was_in_chat = event.old_chat_member.status in ("member", "administrator")
    is_in_chat = event.new_chat_member.status in ("member", "administrator")

    if is_in_chat and not was_in_chat:
        try:
            await send_menu(
                context.bot,
                event.chat.id,
                "🤖 Бот подключён\n\nМеню открывается значком клавиатуры "
                "справа в строке ввода 👇",
            )
        except Exception as error:
            print(f"Не удалось отправить меню в группу {event.chat.id}: {error}")


# ============================================================
# ЕЖЕДНЕВНЫЙ ОТЧЁТ
# ============================================================

async def daily_report(context: ContextTypes.DEFAULT_TYPE):
    today = date_str(moscow_now().date())

    # Только чаты, где сегодня были сообщения
    db = get_db()
    chats = db.execute(
        "SELECT DISTINCT chat_id FROM daily_stats WHERE date = ?", (today,)
    ).fetchall()
    db.close()

    for (chat_id,) in chats:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=make_today_stats_text(chat_id),
                disable_notification=True,
            )
            print(f"Отчёт отправлен в чат {chat_id}")
        except Exception as error:
            print(f"Ошибка отправки отчёта в {chat_id}: {error}")


# ============================================================
# ЗАПУСК
# ============================================================

BOT_COMMANDS = [
    BotCommand("menu", "Показать меню"),
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
    BotCommand("help", "Список команд"),
]


async def post_init(application: Application):
    await application.bot.set_my_commands(
        BOT_COMMANDS, scope=BotCommandScopeDefault()
    )
    await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
    print("Команды Telegram настроены.")


def main():
    if not TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Установи переменную окружения BOT_TOKEN."
        )

    init_database()

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(
        ChatMemberHandler(bot_added_to_group, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    commands = {
        "start": menu_command,
        "menu": menu_command,
        "help": help_command,
        "stats": stats,
        "me": me,
        "week": week,
        "month": month,
        "group": group,
        "activity": activity,
        "hours": hours,
        "achievements": achievements,
        "records": records,
        "fact": fact,
    }
    for name, handler in commands.items():
        app.add_handler(CommandHandler(name, handler))

    # Только новые сообщения: правки (edited_message) не считаем
    app.add_handler(
        MessageHandler(
            filters.UpdateType.MESSAGE & ~filters.COMMAND,
            message_handler,
        )
    )

    if app.job_queue is None:
        print(
            "ВНИМАНИЕ: JobQueue недоступен, ежедневный отчёт работать не будет.\n"
            'Установи: pip install "python-telegram-bot[job-queue]"'
        )
    else:
        app.job_queue.run_daily(
            daily_report,
            time=time(hour=REPORT_HOUR, minute=REPORT_MINUTE, tzinfo=MOSCOW),
            name="daily_report",
        )

    print("================================")
    print("Бот запущен!")
    print("Часовой пояс: Москва (UTC+3)")
    print(f"Ежедневный отчёт: {REPORT_HOUR:02d}:{REPORT_MINUTE:02d}")
    print("================================")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

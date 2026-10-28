"""
Telegram-бот «Виктор» — ИИ-ассистент с играми и утилитами.
Версия: 2.0 (Production Ready)

Исправления и улучшения:
- Асинхронный httpx вместо блокирующего requests
- Правильная работа с историей (system всегда первым)
- Конкретная обработка BadRequest вместо общего Exception
- Валидация callback_data (защита от подделки)
- TTLDict для управления памятью (автоматическая очистка)
- Умный ход бота в крестиках-ноликах
- asyncio.to_thread для синхронных функций tools
- concurrent_updates для параллельной обработки
- Периодическая очистка устаревших данных
- Безопасное удаление сообщений (safe_delete)
- Fallback на plain-text при кривом Markdown
"""

import asyncio
import logging
import os
import random
import re
import time
from contextlib import suppress
from datetime import datetime
from typing import Any

# 1. ✅ ИНИЦИАЛИЗАЦИЯ ЛОГИРОВАНИЯ В САМОМ НАЧАЛЕ
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("victor-bot")

import httpx
import pytz
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# 2. ✅ МЯГКИЙ ИМПОРТ С ЛОГИРОВАНИЕМ ПРИЧИНЫ ОШИБКИ
try:
    import tools
except ImportError as e:
    tools = None
    log.warning("⚠️ Модуль tools не найден — /calc и /search отключены. Причина: %s", e)

try:
    from snake_game import start_snake_game, handle_snake_button
    SNAKE_AVAILABLE = True
except ImportError as e:
    SNAKE_AVAILABLE = False
    log.warning("⚠️ Модуль snake_game не найден — /snake отключён. Причина: %s", e)

# ================= КОНФИГУРАЦИЯ =================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "8785270105"))
except (TypeError, ValueError):
    ADMIN_ID = 8785270105
    log.warning("ADMIN_ID некорректен в .env, используется дефолт: %s", ADMIN_ID)

USE_GROQ = bool(GROQ_API_KEY)
API_URL = (
    "https://api.groq.com/openai/v1/chat/completions"
    if USE_GROQ
    else "https://api.openai.com/v1/chat/completions"
)
API_KEY = GROQ_API_KEY if USE_GROQ else OPENAI_API_KEY
MODEL = "llama-3.3-70b-versatile" if USE_GROQ else "gpt-3.5-turbo"

SYSTEM_PROMPT = (
    "Ты полезный ИИ-ассистент Виктор. Отвечай кратко и по делу на русском языке."
)

# ================= КОНСТАНТЫ =================
MAX_HISTORY = 20                 # максимум не-system сообщений в контексте
HISTORY_TTL = 60 * 60 * 24       # 24 часа — потом историю чистим
GAME_TTL = 60 * 60               # 1 час — брошенные игры удаляем
CLEANUP_INTERVAL = 60 * 10       # раз в 10 минут
HTTP_TIMEOUT = 30.0

# ================= ХРАНИЛИЩА С TTL =================
class TTLDict:
    """Словарь с автоматическим удалением записей старше ttl секунд."""
    
    def __init__(self, ttl: int):
        self._data: dict[int, tuple[float, Any]] = {}
        self._ttl = ttl

    def get(self, key: int, default=None):
        item = self._data.get(key)
        if not item:
            return default
        ts, value = item
        if time.time() - ts > self._ttl:
            self._data.pop(key, None)
            return default
        return value

    def set(self, key: int, value: Any):
        self._data[key] = (time.time(), value)

    def pop(self, key: int, default=None):
        return self._data.pop(key, (None, default))[1]

    def __contains__(self, key: int):
        return self.get(key) is not None

    def __len__(self):
        return len(self._data)

    def cleanup(self):
        """Удаляет устаревшие записи. Возвращает количество удалённых."""
        now = time.time()
        stale = [k for k, (ts, _) in self._data.items() if now - ts > self._ttl]
        for k in stale:
            self._data.pop(k, None)
        return len(stale)


user_histories = TTLDict(ttl=HISTORY_TTL)   # user_id -> list[msg]
tictactoe_games = TTLDict(ttl=GAME_TTL)     # user_id -> dict

# ================= УТИЛИТЫ =================
def get_main_menu() -> InlineKeyboardMarkup:
    """Возвращает главное меню бота."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 ИИ-помощник", callback_data="ai_mode"),
         InlineKeyboardButton(" Игры", callback_data="games_menu")],
        [InlineKeyboardButton("🧹 Очистить память", callback_data="clear_history"),
         InlineKeyboardButton("⏰ Время", callback_data="get_time")],
        [InlineKeyboardButton("📊 Статистика", callback_data="stats")],
    ])


def clean_response(text: str) -> str:
    """Убирает <think>...</think> и лишние пробелы."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def get_trimmed_history(history: list[dict]) -> list[dict]:
    """
    Возвращает историю: system всегда первым, далее последние MAX_HISTORY сообщений.
    ✅ Исправлено: восстанавливает system-промпт, если он отсутствует.
    """
    # Если история пустая или None — создаём с system-промптом
    if not history:
        return [{"role": "system", "content": SYSTEM_PROMPT}]
    
    # Проверяем, есть ли system-промпт первым
    if history[0].get("role") != "system":
        # system отсутствует — добавляем в начало
        history = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    
    # Обрезаем до MAX_HISTORY сообщений после system
    if len(history) <= MAX_HISTORY + 1:
        return history
    return [history[0]] + history[1:][-MAX_HISTORY:]


async def safe_delete(message) -> None:
    """Удаляет сообщение, не падая на любых ошибках Telegram."""
    if message is None:
        return
    with suppress(TelegramError):
        await message.delete()


async def safe_reply_md(message, text: str, **kwargs) -> None:
    """Отправляет сообщение с Markdown, при BadRequest — plain-text."""
    try:
        await message.reply_text(text, parse_mode=ParseMode.MARKDOWN, **kwargs)
    except BadRequest as e:
        log.warning("Markdown сломан (%s), отправляю plain: %r", e, text[:80])
        await message.reply_text(text, **kwargs)


# ================= ИИ-АССИСТЕНТ =================
async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик обычных текстовых сообщений — ИИ-чат."""
    user_id = update.effective_user.id

    if not API_KEY:
        await update.message.reply_text("❌ ИИ не настроен. Добавь API ключ в .env")
        return

    user_message = update.message.text or ""
    history = user_histories.get(user_id) or [{"role": "system", "content": SYSTEM_PROMPT}]
    history.append({"role": "user", "content": user_message})

    thinking_msg = await update.message.reply_text("🤔 Думаю...")

    try:
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }
        
        # ✅ Используем get_trimmed_history для запроса к API
        payload = {
            "model": MODEL,
            "messages": get_trimmed_history(history),
            "temperature": 0.7,
            "max_tokens": 1024,
        }

        # ✅ Асинхронный httpx вместо блокирующего requests
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.post(API_URL, headers=headers, json=payload)

        await safe_delete(thinking_msg)

        if response.status_code != 200:
            log.error("API %s: %s", response.status_code, response.text[:300])
            await update.message.reply_text(
                f"❌ Ошибка API ({response.status_code}):\n{response.text[:200]}"
            )
            return

        data = response.json()
        try:
            ai_response = clean_response(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError) as e:
            log.exception("Неожиданный формат ответа API: %s", e)
            await update.message.reply_text("❌ Некорректный ответ от API")
            return

        history.append({"role": "assistant", "content": ai_response})
        
        # ✅ СОХРАНЯЕМ В TTLDict УЖЕ ОБРЕЗАННУЮ ИСТОРИЮ
        user_histories.set(user_id, get_trimmed_history(history))

        await safe_reply_md(update.message, ai_response)

    except httpx.TimeoutException:
        await safe_delete(thinking_msg)
        await update.message.reply_text("⏱️ ИИ отвечает слишком долго. Попробуй ещё раз.")
    except httpx.HTTPError as e:
        await safe_delete(thinking_msg)
        log.exception("HTTP-ошибка: %s", e)
        await update.message.reply_text(f"❌ Сетевая ошибка: {e}")
    except Exception as e:
        await safe_delete(thinking_msg)
        log.exception("Необработанная ошибка в ai_chat")
        await update.message.reply_text(f"❌ Ошибка: {e}")


async def clear_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /clear — очистка истории диалога."""
    user_id = update.effective_user.id
    user_histories.set(user_id, [{"role": "system", "content": SYSTEM_PROMPT}])
    await update.message.reply_text("🧠 История диалога очищена!", reply_markup=get_main_menu())


# ================= КРЕСТИКИ-НОЛИКИ =================
WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


def check_winner(board: list[str]) -> str | None:
    """Проверяет победителя в крестиках-ноликах."""
    for a, b, c in WIN_LINES:
        if board[a] == board[b] == board[c] and board[a] != " ":
            return board[a]
    return "draw" if " " not in board else None


def get_ttt_keyboard(board: list[str]) -> InlineKeyboardMarkup:
    """Создаёт inline-клавиатуру для крестиков-ноликов."""
    symbols = {"X": "❌", "O": "⭕", " ": ""}
    rows = []
    for i in range(0, 9, 3):
        rows.append([
            InlineKeyboardButton(symbols[board[i + j]], callback_data=f"ttt_{i + j}")
            for j in range(3)
        ])
    rows.append([InlineKeyboardButton("🔄 Новая игра", callback_data="ttt_restart")])
    return InlineKeyboardMarkup(rows)


def parse_ttt_index(data: str) -> int | None:
    """
    Возвращает индекс клетки (0..8) или None для подделанного callback.
    ✅ Защита от IndexError при некорректных данных.
    """
    if not data.startswith("ttt_") or data == "ttt_restart":
        return None
    try:
        idx = int(data.split("_", 1)[1])
    except (ValueError, IndexError):
        return None
    return idx if 0 <= idx <= 8 else None


def bot_best_move(board: list[str]) -> int | None:
    """
    Умный ход бота: сначала выиграть, потом блокировать, потом центр/углы, иначе случайно.
    ✅ Бот теперь может побеждать и блокировать игрока.
    """
    def tries_to_win(symbol: str) -> int | None:
        for i in range(9):
            if board[i] == " ":
                board[i] = symbol
                won = check_winner(board) == symbol
                board[i] = " "
                if won:
                    return i
        return None

    # 1. Выиграть, если можно
    move = tries_to_win("O")
    if move is not None:
        return move
    
    # 2. Заблокировать игрока
    move = tries_to_win("X")
    if move is not None:
        return move

    # 3. Центр, затем углы
    for i in (4, 0, 2, 6, 8):
        if board[i] == " ":
            return i
        
    # 4. Любая свободная клетка
    empty = [i for i, v in enumerate(board) if v == " "]
    return random.choice(empty) if empty else None


async def tictactoe_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /ttt — начать игру в крестики-нолики."""
    user_id = update.effective_user.id
    tictactoe_games.set(user_id, {"board": [" "] * 9, "active": True})
    await update.message.reply_text(
        "🎮 **Крестики-нолики!**\n\nТы играешь за ❌. Твой ход!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_ttt_keyboard([" "] * 9),
    )


async def tictactoe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатий кнопок в крестиках-ноликах."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "ttt_restart":
        tictactoe_games.set(user_id, {"board": [" "] * 9, "active": True})
        await query.edit_message_text(
            "🎮 **Крестики-нолики!**\n\nТвой ход (❌)!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_ttt_keyboard([" "] * 9),
        )
        return

    game = tictactoe_games.get(user_id)
    if not game or not game.get("active"):
        await query.answer("Игра не активна! Начни новую /ttt", show_alert=True)
        return

    idx = parse_ttt_index(data)
    if idx is None:
        await query.answer("Неверный ход!", show_alert=True)
        return

    board = game["board"]
    if board[idx] != " ":
        await query.answer("Клетка занята!", show_alert=True)
        return

    # Ход игрока
    board[idx] = "X"
    winner = check_winner(board)

    if winner:
        game["active"] = False
        tictactoe_games.set(user_id, game)
        text = "🎉 **Ты победил!**" if winner == "X" else "🤝 **Ничья!**"
        await query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_ttt_keyboard(board)
        )
        return

    # Ход бота
    bot_idx = bot_best_move(board)
    if bot_idx is not None:
        board[bot_idx] = "O"
        winner = check_winner(board)
        if winner:
            game["active"] = False
            tictactoe_games.set(user_id, game)
            text = "🤖 **Бот победил!**" if winner == "O" else "🤝 **Ничья!**"
            await query.edit_message_text(
                text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_ttt_keyboard(board)
            )
            return

    tictactoe_games.set(user_id, game)
    await query.edit_message_reply_markup(reply_markup=get_ttt_keyboard(board))


# ================= МЕНЮ =================
MENU_ACTIONS = {"ai_mode", "games_menu", "back_to_main", "clear_history", "get_time", "stats"}


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик кнопок главного меню."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data not in MENU_ACTIONS:
        await query.answer("Неизвестное действие", show_alert=True)
        return

    if data == "ai_mode":
        text = " **ИИ-помощник активирован!**\n\nПросто напиши мне любой вопрос — я отвечу."
    elif data == "games_menu":
        lines = ["🎮 **Игры**", ""]
        lines.append("• Крестики-нолики: /ttt")
        if SNAKE_AVAILABLE:
            lines.append("• Змейка: /snake")
        text = "\n".join(lines)
    elif data == "back_to_main":
        text = "🏠 **Главное меню**\n\nВыбери действие:"
    elif data == "clear_history":
        user_id = update.effective_user.id
        user_histories.set(user_id, [{"role": "system", "content": SYSTEM_PROMPT}])
        text = " История очищена!"
    elif data == "get_time":
        moscow = pytz.timezone("Europe/Moscow")
        now = datetime.now(moscow)
        text = f"⏰ **Сейчас:** {now.strftime('%H:%M:%S')}\n📅 **Дата:** {now.strftime('%d.%m.%Y')}"
    else:  # stats
        active_games = sum(
            1 for uid in tictactoe_games._data 
            if (g := tictactoe_games.get(uid)) and g.get("active")
        )
        text = (
            f" **Статистика:**\n\n"
            f"Пользователей в памяти: {len(user_histories)}\n"
            f"Активных партий (крестики): {active_games}"
        )

    await query.edit_message_text(
        text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu()
    )


# ================= АДМИН =================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /admin — панель администратора."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда только для администратора")
        return
    await update.message.reply_text(
        f"🔧 **Панель администратора**\n\n"
        f"Твой ID: `{ADMIN_ID}`\n"
        f"Пользователей: {len(user_histories)}\n"
        f"Активных игр: {len(tictactoe_games)}",
        parse_mode=ParseMode.MARKDOWN,
    )


# ================= КОМАНДЫ-УТИЛИТЫ =================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /help — справка."""
    lines = [
        "📖 **Список команд:**", "",
        "🔹 /start — Главное меню",
        "🔹 /help — Эта справка",
        "🔹 /ttt — Крестики-нолики",
    ]
    if SNAKE_AVAILABLE:
        lines.append("🔹 /snake — Игра «Змейка» 🐍")
    if tools:
        lines += [
            "🔹 /calc <выражение> — Калькулятор",
            "🔹 /search <запрос> — Поиск в интернете",
            "🔹 /time_cmd — Текущее время",
        ]
    lines += [
        "🔹 /clear — Очистить историю ИИ",
        " /admin — Панель администратора",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def calc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /calc — калькулятор."""
    if not tools:
        await update.message.reply_text(" Калькулятор недоступен")
        return
    expression = " ".join(context.args)
    if not expression:
        await update.message.reply_text(
            "❌ Введите выражение. Пример: `/calc 2+2*2`", parse_mode=ParseMode.MARKDOWN
        )
        return
    try:
        # ✅ asyncio.to_thread — не блокирует event loop
        result = await asyncio.to_thread(tools.calculate, expression)
        await update.message.reply_text(str(result))
    except Exception as e:
        log.exception("calc error")
        await update.message.reply_text(f"❌ Ошибка вычисления: {e}")


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /search — поиск в интернете."""
    if not tools:
        await update.message.reply_text("❌ Поиск недоступен")
        return
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text(
            "❌ Введите запрос. Пример: `/search погода в москве`", parse_mode=ParseMode.MARKDOWN
        )
        return

    thinking_msg = await update.message.reply_text("🔍 Ищу в интернете...")
    try:
        # ✅ asyncio.to_thread — не блокирует event loop
        result = await asyncio.to_thread(tools.search_internet, query)
    except Exception as e:
        log.exception("search error")
        await safe_delete(thinking_msg)
        await update.message.reply_text(f"❌ Поиск не удался: {e}")
        return
    await safe_delete(thinking_msg)
    await update.message.reply_text(str(result))


async def time_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /time_cmd — текущее время."""
    if not tools:
        await update.message.reply_text("❌ Утилита времени недоступна")
        return
    try:
        result = await asyncio.to_thread(tools.get_current_time)
        await update.message.reply_text(str(result))
    except Exception as e:
        log.exception("time error")
        await update.message.reply_text(f"❌ Ошибка: {e}")


# ================= СТАРТ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /start — приветствие."""
    first_name = update.effective_user.first_name or "друг"
    text = (
        f"👋 **Привет, {first_name}!** Я Виктор — ИИ-ассистент.\n\n"
        "Я умею:\n"
        "• 🤖 Отвечать на вопросы (просто напиши мне)\n"
        "• 🎮 Играть в игры (/ttt, /snake)\n"
        "• 🧮 Считать математику (/calc)\n"
        "• 🔍 Искать в интернете (/search)\n\n"
        "**Нажми кнопку ниже или просто напиши вопрос!**"
    )
    await safe_reply_md(update.message, text, reply_markup=get_main_menu())


# ================= ФОНОВАЯ ОЧИСТКА =================
async def cleanup_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Периодическая очистка устаревших записей из TTLDict."""
    h = user_histories.cleanup()
    g = tictactoe_games.cleanup()
    if h or g:
        log.info("TTL-cleanup: удалено историй=%d, игр=%d", h, g)


# ================= ЗАПУСК =================
def main() -> None:
    """Главная функция запуска бота."""
    if not TELEGRAM_TOKEN:
        log.critical("❌ TELEGRAM_BOT_TOKEN не найден!")
        return

    log.info(" Модель: %s", "Groq (Llama)" if USE_GROQ else "OpenAI (GPT)")
    log.info("🔑 API ключ: %s", "найден" if API_KEY else "НЕ НАЙДЕН")
    log.info("👤 Админ ID: %s", ADMIN_ID)

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .concurrent_updates(True)   # ✅ параллельная обработка апдейтов
        .build()
    )

    # Регистрируем обработчики команд
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("calc", calc_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("time_cmd", time_cmd))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("clear", clear_history_cmd))
    app.add_handler(CommandHandler("ttt", tictactoe_command))

    # Обработчики для змейки (если модуль доступен)
    if SNAKE_AVAILABLE:
        app.add_handler(CommandHandler("snake", start_snake_game))
        app.add_handler(CallbackQueryHandler(handle_snake_button, pattern=r"^snake_"))

    # Обработчики для крестиков-ноликов
    app.add_handler(CallbackQueryHandler(tictactoe_callback, pattern=r"^ttt_"))
    
    # ✅ Явный pattern для меню — неизвестные callback не «глотаются»
    pattern = "^(" + "|".join(MENU_ACTIONS) + ")$"
    app.add_handler(CallbackQueryHandler(button_handler, pattern=pattern))
    
    # Обработчик обычных текстовых сообщений (ИИ-чат)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat))

    # Периодическая очистка TTL-словарей (если job_queue доступен)
    if app.job_queue:
        app.job_queue.run_repeating(
            cleanup_job, interval=CLEANUP_INTERVAL, first=CLEANUP_INTERVAL
        )

    log.info("✅ Бот успешно запущен и готов к работе!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
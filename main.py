import os
import re
import json
import random
import sqlite3
import asyncio
import requests
from requests.exceptions import Timeout, RequestException
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, InvalidToken
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ContextTypes, CallbackQueryHandler
)
from datetime import datetime
import pytz

# Попытка импорта tools с защитой
try:
    import tools
    TOOLS_AVAILABLE = True
except ImportError:
    TOOLS_AVAILABLE = False
    print("⚠️ tools.py не найден! Команды /calc, /search, /time_cmd будут недоступны.")

# Загружаем переменные окружения
load_dotenv()

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Безопасная загрузка ADMIN_ID
ADMIN_ID = None
try:
    admin_id_env = os.getenv("ADMIN_ID", "8785270105")
    ADMIN_ID = int(admin_id_env) if admin_id_env else None
except (ValueError, TypeError):
    print("⚠️ ADMIN_ID не задан или некорректен. Админ-команды отключены.")

ADS_DB = "ads.db"

USE_GROQ = bool(GROQ_API_KEY)
API_URL = "https://api.groq.com/openai/v1/chat/completions" if USE_GROQ else "https://api.openai.com/v1/chat/completions"
API_KEY = GROQ_API_KEY if USE_GROQ else OPENAI_API_KEY
MODEL = "llama-3.3-70b-versatile" if USE_GROQ else "gpt-3.5-turbo"

print(f"🤖 Используем: {'Groq (Llama)' if USE_GROQ else 'OpenAI (GPT)'}")
print(f"🔑 API ключ: {'найден' if API_KEY else 'НЕ НАЙДЕН'}")
print(f"🛡️ ADMIN_ID: {ADMIN_ID}")

# ================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =================
MAX_HISTORY = 20
user_histories = {}
user_last_activity = {}  # для очистки памяти
tictactoe_games = {}
guess_games = {}
quiz_games = {}

# ================= БАЗА ДАННЫХ (SQLite для объявлений) =================
def init_db():
    """Инициализация SQLite для объявлений (замена JSON)"""
    try:
        conn = sqlite3.connect(ADS_DB)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS ads
                     (id INTEGER PRIMARY KEY AUTOINCREMENT,
                      title TEXT NOT NULL,
                      link TEXT NOT NULL,
                      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
        conn.commit()
        conn.close()
        print("✅ База объявлений готова (SQLite)")
    except Exception as e:
        print(f"❌ Ошибка инициализации БД: {e}")

def load_ads():
    try:
        conn = sqlite3.connect(ADS_DB)
        c = conn.cursor()
        c.execute("SELECT id, title, link FROM ads ORDER BY created_at DESC LIMIT 10")
        rows = c.fetchall()
        conn.close()
        return [{"id": r[0], "title": r[1], "link": r[2]} for r in rows]
    except Exception as e:
        print(f"Ошибка загрузки объявлений: {e}")
        return []

def save_ad(title, link):
    try:
        conn = sqlite3.connect(ADS_DB)
        c = conn.cursor()
        c.execute("INSERT INTO ads (title, link) VALUES (?, ?)", (title, link))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Ошибка сохранения объявления: {e}")
        return False

def remove_ad(ad_id):
    try:
        conn = sqlite3.connect(ADS_DB)
        c = conn.cursor()
        c.execute("DELETE FROM ads WHERE id = ?", (ad_id,))
        conn.commit()
        deleted = c.rowcount
        conn.close()
        return deleted > 0
    except Exception as e:
        print(f"Ошибка удаления объявления: {e}")
        return False

# ================= УТИЛИТЫ =================
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🤖 ИИ-помощник", callback_data='ai_mode'),
         InlineKeyboardButton("🎮 Игры", callback_data='games_menu')],
        [InlineKeyboardButton("📋 Объявления", callback_data='ads_menu'),
         InlineKeyboardButton("🧠 Очистить память", callback_data='clear_history')],
        [InlineKeyboardButton("⏰ Время", callback_data='get_time'),
         InlineKeyboardButton("📊 Статистика", callback_data='stats')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_ads_keyboard(ads):
    keyboard = []
    for ad in ads[:10]:
        title = ad.get('title', 'Без названия')[:40]
        link = ad.get('link', '')
        if link:
            keyboard.append([InlineKeyboardButton(f"🔗 {title}", url=link)])
    if not keyboard:
        keyboard.append([InlineKeyboardButton("Объявлений пока нет", callback_data='no_ads')])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data='back_to_main')])
    return InlineKeyboardMarkup(keyboard)

def clean_response(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return text.strip()

async def send_long_message(target, text, parse_mode=ParseMode.MARKDOWN):
    """Отправка длинных сообщений с разбивкой по 4000 символов"""
    MAX_LEN = 4000
    if len(text) <= MAX_LEN:
        await target.reply_text(text, parse_mode=parse_mode)
        return
    
    chunks = [text[i:i+MAX_LEN] for i in range(0, len(text), MAX_LEN)]
    for i, chunk in enumerate(chunks):
        try:
            await target.reply_text(chunk, parse_mode=parse_mode)
            if i < len(chunks) - 1:
                await asyncio.sleep(0.3)  # защита от flood
        except Exception as e:
            print(f"Ошибка отправки чанка {i+1}: {e}")
            break

async def safe_answer(query, text=None, show_alert=False):
    """Безопасный ответ на callback_query (ловит устаревшие запросы)"""
    try:
        await query.answer(text, show_alert=show_alert)
    except BadRequest as e:
        print(f"⚠️ Устаревший callback: {e}")
    except Exception as e:
        print(f"⚠️ Ошибка answer: {e}")

async def safe_edit(query, text, reply_markup=None, parse_mode=ParseMode.MARKDOWN):
    """Безопасное редактирование сообщения"""
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return True
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return True
        print(f"⚠️ Не удалось отредактировать: {e}")
        return False
    except Exception as e:
        print(f"⚠️ Ошибка edit_message: {e}")
        return False

# ================= ОЧИСТКА ПАМЯТИ =================
async def cleanup_inactive_users(context: ContextTypes.DEFAULT_TYPE):
    """Периодическая очистка неактивных пользователей (раз в час)"""
    now = datetime.now().timestamp()
    INACTIVE_SECONDS = 3600  # 1 час
    
    # Очистка историй ИИ
    to_remove = [uid for uid, last in user_last_activity.items() 
                 if now - last > INACTIVE_SECONDS]
    for uid in to_remove:
        user_histories.pop(uid, None)
        user_last_activity.pop(uid, None)
        tictactoe_games.pop(uid, None)
        guess_games.pop(uid, None)
        quiz_games.pop(uid, None)
    
    if to_remove:
        print(f"🧹 Очищено {len(to_remove)} неактивных пользователей")

# ================= ИИ-АССИСТЕНТ =================
async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not API_KEY:
        await update.message.reply_text("❌ ИИ не настроен. Добавьте API ключ в .env")
        return
    
    user_last_activity[user_id] = datetime.now().timestamp()
    
    if user_id not in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": "Ты полезный ИИ-ассистент. Отвечай кратко и по делу на русском языке."}
        ]
    
    user_message = update.message.text
    user_histories[user_id].append({"role": "user", "content": user_message})
    
    thinking_msg = await update.message.reply_text("🤔 Думаю...")
    
    try:
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        messages = [{"role": m["role"], "content": m["content"]} 
                   for m in user_histories[user_id][-MAX_HISTORY:]]
        data = {"model": MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 1024}
        
        response = requests.post(API_URL, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            ai_response = clean_response(result['choices'][0]['message']['content'])
            user_histories[user_id].append({"role": "assistant", "content": ai_response})
            
            await thinking_msg.delete()
            await send_long_message(update.message, ai_response)
            
            # Ограничение истории
            if len(user_histories[user_id]) > MAX_HISTORY + 1:
                user_histories[user_id] = [user_histories[user_id][0]] + user_histories[user_id][-MAX_HISTORY:]
        else:
            await thinking_msg.delete()
            error_text = response.text[:200] if response.text else "неизвестная ошибка"
            await update.message.reply_text(f"❌ Ошибка API ({response.status_code}):\n{error_text}")
            
    except Timeout:
        await thinking_msg.delete()
        await update.message.reply_text("⏱️ Превышено время ожидания ответа от ИИ. Попробуйте ещё раз.")
    except RequestException as e:
        await thinking_msg.delete()
        await update.message.reply_text(f"❌ Сетевая ошибка: {str(e)[:100]}")
    except Exception as e:
        await thinking_msg.delete()
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": "Ты полезный ИИ-ассистент. Отвечай кратко и по делу на русском языке."}
        ]
    
    # Отвечаем в зависимости от типа обновления
    if update.callback_query:
        await safe_answer(update.callback_query, "🧠 История очищена!")
        await safe_edit(update.callback_query, "🧠 История очищена!", reply_markup=get_main_menu())
    elif update.message:
        await update.message.reply_text("🧠 История диалога очищена!", reply_markup=get_main_menu())

# ================= КРЕСТИКИ-НОЛИКИ =================
def check_winner(board):
    wins = [(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
    for a,b,c in wins:
        if board[a]==board[b]==board[c] and board[a]!=' ':
            return board[a]
    return 'draw' if ' ' not in board else None

def get_ttt_keyboard(board):
    keyboard = []
    for i in range(0,9,3):
        row = []
        for j in range(3):
            idx = i+j
            symbol = '❌' if board[idx]=='X' else '⭕' if board[idx]=='O' else '⬜'
            row.append(InlineKeyboardButton(symbol, callback_data=f"ttt_{idx}"))
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔄 Новая игра", callback_data="ttt_restart")])
    return InlineKeyboardMarkup(keyboard)

async def tictactoe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_last_activity[user_id] = datetime.now().timestamp()
    tictactoe_games[user_id] = {'board': [' ']*9, 'active': True}
    await update.message.reply_text(
        "🎮 **Крестики-нолики!**\n\nТы играешь за ❌. Твой ход!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_ttt_keyboard(tictactoe_games[user_id]['board'])
    )

async def tictactoe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    
    user_id = query.from_user.id
    data = query.data
    
    if data == "ttt_restart":
        tictactoe_games[user_id] = {'board': [' ']*9, 'active': True}
        user_last_activity[user_id] = datetime.now().timestamp()
        await safe_edit(query, "🎮 **Крестики-нолики!**\n\nТвой ход (❌)!",
                       reply_markup=get_ttt_keyboard(tictactoe_games[user_id]['board']))
        return
    
    if user_id not in tictactoe_games or not tictactoe_games[user_id]['active']:
        await safe_answer(query, "Игра не активна! Начни новую /ttt", show_alert=True)
        return
    
    try:
        idx = int(data.split("_")[1])
    except (IndexError, ValueError):
        return
    
    board = tictactoe_games[user_id]['board']
    if board[idx] != ' ':
        await safe_answer(query, "Клетка занята!", show_alert=True)
        return
    
    board[idx] = 'X'
    user_last_activity[user_id] = datetime.now().timestamp()
    winner = check_winner(board)
    
    if winner:
        tictactoe_games[user_id]['active'] = False
        result_text = f"🎉 **{'ТЫ ПОБЕДИЛ!' if winner=='X' else 'НИЧЬЯ!'}**"
        await safe_edit(query, result_text, reply_markup=get_ttt_keyboard(board))
        return
    
    empty = [i for i,v in enumerate(board) if v==' ']
    if empty:
        board[random.choice(empty)] = 'O'
        winner = check_winner(board)
        if winner:
            tictactoe_games[user_id]['active'] = False
            result_text = f"🤖 **{'БОТ ПОБЕДИЛ!' if winner=='O' else 'НИЧЬЯ!'}**"
            await safe_edit(query, result_text, reply_markup=get_ttt_keyboard(board))
            return
    
    await safe_edit(query, "🎮 Твой ход (❌):", reply_markup=get_ttt_keyboard(board))

# ================= ИГРА: УГАДАЙ ЧИСЛО =================
async def guess_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_last_activity[user_id] = datetime.now().timestamp()
    guess_games[user_id] = random.randint(1, 100)
    await update.message.reply_text(
        "🎲 **Угадай число!**\n\nЯ загадал число от 1 до 100.\nНапиши свой вариант числом:",
        parse_mode=ParseMode.MARKDOWN
    )

async def guess_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    secret = guess_games.get(user_id)
    if secret is None:
        return
    
    user_last_activity[user_id] = datetime.now().timestamp()
    
    try:
        user_guess = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введи именно число!")
        return
    
    if user_guess < 1 or user_guess > 100:
        await update.message.reply_text("❌ Число должно быть от 1 до 100!")
        return
    
    if user_guess < secret:
        await update.message.reply_text("📈 Больше!")
    elif user_guess > secret:
        await update.message.reply_text("📉 Меньше!")
    else:
        del guess_games[user_id]
        await update.message.reply_text(
            f"🎉 **Победа!** Ты угадал число `{secret}`!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu()
        )

# ================= ИГРА: КАМЕНЬ-НОЖНИЦЫ-БУМАГА =================
RPS_EMOJI = {"rock": "🪨 Камень", "paper": "📄 Бумага", "scissors": "✂️ Ножницы"}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

def get_rps_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🪨", callback_data="rps_rock"),
        InlineKeyboardButton("📄", callback_data="rps_paper"),
        InlineKeyboardButton("✂️", callback_data="rps_scissors")
    ]])

async def rps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_last_activity[user_id] = datetime.now().timestamp()
    await update.message.reply_text(
        "🪨📄✂️ **Камень-ножницы-бумага!**\n\nВыбери свой ход:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_rps_keyboard()
    )

async def rps_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    
    if query.data == "rps_again":
        await safe_edit(query, "🪨✂️ **Выбери свой ход:**", reply_markup=get_rps_keyboard())
        return
    
    user_choice = query.data.replace("rps_", "")
    bot_choice = random.choice(list(RPS_BEATS.keys()))
    
    if user_choice == bot_choice:
        result = "🤝 **Ничья!**"
    elif RPS_BEATS[user_choice] == bot_choice:
        result = "🎉 **Ты победил!**"
    else:
        result = "🤖 **Бот победил!**"
    
    await safe_edit(
        query,
        f"Ты: {RPS_EMOJI[user_choice]}\nБот: {RPS_EMOJI[bot_choice]}\n\n{result}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Ещё раз", callback_data="rps_again")]])
    )

# ================= ИГРА: ВИКТОРИНА =================
QUIZ_QUESTIONS = [
    {"q": "Какая планета ближе всех к Солнцу?", "a": ["Венера", "Меркурий", "Марс", "Земля"], "c": 1},
    {"q": "Сколько ног у паука?", "a": ["6", "8", "4", "10"], "c": 1},
    {"q": "Столица Японии?", "a": ["Пекин", "Сеул", "Токио", "Бангкок"], "c": 2},
    {"q": "Какой газ мы вдыхаем?", "a": ["Кислород", "Углекислый газ", "Азот", "Гелий"], "c": 0},
    {"q": "Сколько цветов у радуги?", "a": ["5", "6", "7", "8"], "c": 2},
]

def get_quiz_keyboard(q_idx):
    keyboard = []
    for i, ans in enumerate(QUIZ_QUESTIONS[q_idx]["a"]):
        keyboard.append([InlineKeyboardButton(ans, callback_data=f"quiz_{q_idx}_{i}")])
    return InlineKeyboardMarkup(keyboard)

async def quiz_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_last_activity[user_id] = datetime.now().timestamp()
    quiz_games[user_id] = {"q": 0, "score": 0}
    await update.message.reply_text(
        f"🧠 **Викторина!** Вопрос 1 из {len(QUIZ_QUESTIONS)}:\n\n{QUIZ_QUESTIONS[0]['q']}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_quiz_keyboard(0)
    )

async def quiz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    
    user_id = query.from_user.id
    
    try:
        _, q_idx_str, ans_idx_str = query.data.split("_")
        q_idx = int(q_idx_str)
        ans_idx = int(ans_idx_str)
    except (ValueError, AttributeError):
        return
    
    game = quiz_games.get(user_id)
    if game is None or game["q"] != q_idx:
        await safe_answer(query, "Вопрос устарел! Начни заново /quiz", show_alert=True)
        return
    
    q = QUIZ_QUESTIONS[q_idx]
    if ans_idx == q["c"]:
        game["score"] += 1
        await safe_answer(query, "✅ Верно!")
    else:
        await safe_answer(query, f"❌ Неверно! Правильно: {q['a'][q['c']]}", show_alert=True)
    
    game["q"] += 1
    user_last_activity[user_id] = datetime.now().timestamp()
    
    if game["q"] < len(QUIZ_QUESTIONS):
        nq = QUIZ_QUESTIONS[game["q"]]
        await safe_edit(
            query,
            f"🧠 **Викторина!** Вопрос {game['q']+1} из {len(QUIZ_QUESTIONS)}:\n\n{nq['q']}",
            reply_markup=get_quiz_keyboard(game["q"])
        )
    else:
        score = game["score"]
        del quiz_games[user_id]
        await safe_edit(
            query,
            f"🏁 **Викторина окончена!**\n\nТвой результат: `{score}` из `{len(QUIZ_QUESTIONS)}`",
            reply_markup=get_main_menu()
        )

# ================= ОБРАБОТЧИК КНОПОК МЕНЮ =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_answer(query)
    
    data = query.data
    user_id = update.effective_user.id
    user_last_activity[user_id] = datetime.now().timestamp()
    
    if data == 'ai_mode':
        await safe_edit(query, 
            "🤖 **ИИ-помощник активирован!**\n\nПросто напиши мне любой вопрос, и я отвечу!",
            reply_markup=get_main_menu())
    elif data == 'games_menu':
        await safe_edit(query,
            "🎮 **Игры**\n\n• Крестики-нолики: /ttt\n• Угадай число: /guess\n• Камень-ножницы-бумага: /rps\n• Викторина: /quiz",
            reply_markup=get_main_menu())
    elif data == 'ads_menu':
        ads = load_ads()
        if ads:
            await safe_edit(query,
                "📋 **Актуальные объявления:**\n\nНажми на товар, чтобы открыть:",
                reply_markup=get_ads_keyboard(ads))
        else:
            await safe_edit(query,
                "📋 **Объявления**\n\nПока нет активных объявлений. Загляни позже!",
                reply_markup=get_main_menu())
    elif data == 'back_to_main':
        await safe_edit(query, "👋 **Главное меню**\n\nВыбери действие:", reply_markup=get_main_menu())
    elif data == 'no_ads':
        await safe_answer(query, "Объявлений пока нет", show_alert=True)
    elif data == 'clear_history':
        if user_id in user_histories:
            user_histories[user_id] = [{"role": "system", "content": "Ты полезный ИИ-ассистент."}]
        await safe_edit(query, "🧠 История очищена!", reply_markup=get_main_menu())
    elif data == 'get_time':
        try:
            moscow_tz = pytz.timezone('Europe/Moscow')
            now = datetime.now(moscow_tz).strftime("%H:%M:%S")
            date = datetime.now(moscow_tz).strftime("%d.%m.%Y")
            await safe_edit(query, f"⏰ **Сейчас:** {now}\n📅 **Дата:** {date}", reply_markup=get_main_menu())
        except Exception as e:
            await safe_edit(query, f"❌ Ошибка получения времени: {e}", reply_markup=get_main_menu())
    elif data == 'stats':
        text = (f"📊 **Статистика:**\n\n"
                f"Пользователей с историей: {len(user_histories)}\n"
                f"Активных игр КН: {sum(1 for g in tictactoe_games.values() if g.get('active', False))}\n"
                f"Активных игр 'Угадай число': {len(guess_games)}\n"
                f"Объявлений в БД: {len(load_ads())}")
        await safe_edit(query, text, reply_markup=get_main_menu())

# ================= КОМАНДА /admin =================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if ADMIN_ID is None:
        await update.message.reply_text("❌ ADMIN_ID не настроен в .env")
        return
    
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда только для администратора")
        return
    
    ads = load_ads()
    text = (f"🔧 **Панель администратора**\n\n"
            f"Твой ID: `{user_id}`\n"
            f"Всего объявлений: {len(ads)}\n"
            f"Пользователей с историей: {len(user_histories)}\n\n"
            f"Команды:\n"
            f"• `/add_ad Название | Ссылка`\n"
            f"• `/remove_ad <номер>`")
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ================= СТАРТ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    first_name = update.effective_user.first_name
    user_id = update.effective_user.id
    user_last_activity[user_id] = datetime.now().timestamp()
    
    tools_section = ""
    if TOOLS_AVAILABLE:
        tools_section = "• 🧮 Считать математику (/calc)\n• 🔍 Искать в интернете (/search)\n"
    else:
        tools_section = "• 🧮 Инструменты недоступны (tools.py отсутствует)\n"
    
    await update.message.reply_text(
        f"👋 **Привет, {first_name}!** Я Виктор — ИИ-ассистент.\n\n"
        f"Я умею:\n"
        f"• 🤖 Отвечать на вопросы (просто напиши мне)\n"
        f"• 🎮 Играть в игры (/ttt, /guess, /rps, /quiz)\n"
        f"• 📋 Показывать объявления\n"
        f"{tools_section}\n"
        f"**Нажми на любую кнопку ниже или просто напиши вопрос!**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu()
    )

# ================= ОБЪЯВЛЕНИЯ =================
async def add_ad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if ADMIN_ID is None or user_id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда только для администратора")
        return
    
    if not update.message.text:
        await update.message.reply_text("❌ Неверный формат.")
        return
    
    text = update.message.text.replace('/add_ad', '', 1).strip()
    if not text:
        await update.message.reply_text(
            "ℹ️ Использование: `/add_ad Название | Ссылка`\n\nПример: `/add_ad iPhone 13 | https://avito.ru/...`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    
    if '|' not in text:
        await update.message.reply_text("❌ Формат: `Название | Ссылка`", parse_mode=ParseMode.MARKDOWN)
        return
    
    parts = text.split('|', 1)
    title = parts[0].strip()
    link = parts[1].strip()
    
    if not title or not link:
        await update.message.reply_text("❌ Название и ссылка не могут быть пустыми.")
        return
    
    if save_ad(title, link):
        await update.message.reply_text(f"✅ Объявление добавлено: {title}\n🔗 {link}")
    else:
        await update.message.reply_text("❌ Не удалось сохранить объявление")

async def remove_ad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if ADMIN_ID is None or user_id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда только для администратора")
        return
    
    ads = load_ads()
    if not ads:
        await update.message.reply_text("📋 Список объявлений пуст")
        return
    
    if context.args:
        try:
            num = int(context.args[0])
            if 1 <= num <= len(ads):
                ad_to_remove = ads[num - 1]
                if remove_ad(ad_to_remove['id']):
                    await update.message.reply_text(f"✅ Удалено: {ad_to_remove['title']}")
                else:
                    await update.message.reply_text("❌ Не удалось удалить")
            else:
                await update.message.reply_text(f"❌ Номер должен быть от 1 до {len(ads)}")
        except ValueError:
            await update.message.reply_text("❌ Введите корректный номер", parse_mode=ParseMode.MARKDOWN)
        return
    
    text = "📋 **Объявления для удаления:**\n\n"
    for i, ad in enumerate(ads):
        text += f"`{i+1}`. {ad['title']}\n"
    text += "\nОтправь `/remove_ad <номер>`, чтобы удалить."
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ================= ИНСТРУМЕНТЫ =================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📋 **Список команд:**\n\n"
        "🔹 /start - Главное меню\n"
        "🔹 /help - Эта справка\n"
        "🔹 /ttt - Крестики-нолики\n"
        "🔹 /guess - Угадай число\n"
        "🔹 /rps - Камень-ножницы-бумага\n"
        "🔹 /quiz - Викторина\n"
        "🔹 /time_cmd - Текущее время\n"
        "🔹 /clear - Очистить историю ИИ\n"
        "🔹 /stats - Статистика\n"
    )
    
    if TOOLS_AVAILABLE:
        help_text += (
            "🔹 /calc <выражение> - Калькулятор\n"
            "🔹 /search <запрос> - Поиск в интернете\n"
        )
    
    if ADMIN_ID:
        help_text += (
            "\n**Только для админа:**\n"
            "🔹 /admin - Панель администратора\n"
            "🔹 /add_ad - Добавить объявление\n"
            "🔹 /remove_ad - Удалить объявление"
        )
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def calc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not TOOLS_AVAILABLE:
        await update.message.reply_text("❌ Калькулятор недоступен: tools.py не найден")
        return
    
    expression = " ".join(context.args) if context.args else ""
    if not expression:
        await update.message.reply_text("❌ Введите выражение. Пример: `/calc 2+2*2`", parse_mode=ParseMode.MARKDOWN)
        return
    
    try:
        result = tools.calculate(expression)
        await update.message.reply_text(f"🧮 `{expression}` = `{result}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка вычисления: {str(e)[:100]}")

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not TOOLS_AVAILABLE:
        await update.message.reply_text("❌ Поиск недоступен: tools.py не найден")
        return
    
    query = " ".join(context.args) if context.args else ""
    if not query:
        await update.message.reply_text("❌ Введите запрос. Пример: `/search погода в москве`", parse_mode=ParseMode.MARKDOWN)
        return
    
    thinking_msg = await update.message.reply_text("🔍 Ищу в интернете...")
    try:
        result = tools.search_internet(query)
        await thinking_msg.delete()
        await send_long_message(update.message, result)
    except Exception as e:
        await thinking_msg.delete()
        await update.message.reply_text(f"❌ Ошибка поиска: {str(e)[:200]}")

async def time_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not TOOLS_AVAILABLE:
        await update.message.reply_text("❌ Время недоступно: tools.py не найден")
        return
    try:
        await update.message.reply_text(tools.get_current_time())
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ads = load_ads()
    text = (f"📊 **Статистика:**\n\n"
            f"Пользователей с историей: {len(user_histories)}\n"
            f"Активных игр КН: {sum(1 for g in tictactoe_games.values() if g.get('active', False))}\n"
            f"Активных игр 'Угадай число': {len(guess_games)}\n"
            f"Объявлений в БД: {len(ads)}")
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ================= ОБРАБОТЧИК ОШИБОК =================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик ошибок"""
    print(f"⚠️ Ошибка: {context.error}")
    if update and hasattr(update, 'effective_message') and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"❌ Произошла ошибка. Попробуйте ещё раз или используйте /start"
            )
        except Exception:
            pass

# ================= ЗАПУСК =================
def main():
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не найден в .env!")
        return
    
    # Инициализация БД
    init_db()
    
    print("🚀 Запускаем бота...")
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Задача очистки памяти (каждый час)
    job_queue = application.job_queue
    job_queue.run_repeating(cleanup_inactive_users, interval=3600, first=60)
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("add_ad", add_ad_command))
    application.add_handler(CommandHandler("remove_ad", remove_ad_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("ttt", tictactoe_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("guess", guess_command))
    application.add_handler(CommandHandler("rps", rps_command))
    application.add_handler(CommandHandler("quiz", quiz_command))
    
    # Инструменты (только если tools.py доступен)
    if TOOLS_AVAILABLE:
        application.add_handler(CommandHandler("calc", calc_command))
        application.add_handler(CommandHandler("search", search_command))
        application.add_handler(CommandHandler("time_cmd", time_cmd))
    
    # Обработчики кнопок (паттерны СТРОГО ПЕРЕД button_handler)
    application.add_handler(CallbackQueryHandler(tictactoe_callback, pattern='^ttt_'))
    application.add_handler(CallbackQueryHandler(rps_callback, pattern='^rps_'))
    application.add_handler(CallbackQueryHandler(quiz_callback, pattern='^quiz_'))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Текстовые сообщения — используем filters.Create для динамического фильтра
    # Это замена устаревшему filters.BaseFilter
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & 
        filters.Create(lambda _, msg: msg.from_user and msg.from_user.id in guess_games),
        guess_handler
    ))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat))
    
    # Глобальный обработчик ошибок
    application.add_error_handler(error_handler)
    
    print("✅ Бот запущен! Ожидаем сообщения...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
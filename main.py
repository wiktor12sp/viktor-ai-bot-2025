import os
import re
import json
import random
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from datetime import datetime
import pytz
import tools

# Загружаем переменные окружения
load_dotenv()

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8785270105"))

USE_GROQ = bool(GROQ_API_KEY)
API_URL = "https://api.groq.com/openai/v1/chat/completions" if USE_GROQ else "https://api.openai.com/v1/chat/completions"
API_KEY = GROQ_API_KEY if USE_GROQ else OPENAI_API_KEY
MODEL = "llama-3.3-70b-versatile" if USE_GROQ else "gpt-3.5-turbo"

print(f"🤖 Используем: {'Groq (Llama)' if USE_GROQ else 'OpenAI (GPT)'}")
print(f"🔑 API ключ: {'найден' if API_KEY else 'НЕ НАЙДЕН'}")
print(f"👤 Админ ID: {ADMIN_ID}")

# ================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =================
MAX_HISTORY = 20
user_histories = {}
tictactoe_games = {}
ADS_FILE = "ads_data.json"

# ================= УТИЛИТЫ =================
def load_ads():
    """Загружает объявления из файла"""
    try:
        with open(ADS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"Ошибка загрузки объявлений: {e}")
        return []

def save_ads(ads):
    """Сохраняет объявления в файл"""
    try:
        with open(ADS_FILE, 'w', encoding='utf-8') as f:
            json.dump(ads, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения объявлений: {e}")

def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("🤖 ИИ-помощник", callback_data='ai_mode'),
         InlineKeyboardButton("🎮 Игры", callback_data='games_menu')],
        [InlineKeyboardButton("📋 Объявления", callback_data='ads_menu'),
         InlineKeyboardButton("🧠 Очистить память", callback_data='clear_history')],
        [InlineKeyboardButton("⏰ Время", callback_data='get_time'),
         InlineKeyboardButton(" Статистика", callback_data='stats')]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_ads_keyboard(ads):
    """Создаёт кнопки с ссылками на объявления"""
    keyboard = []
    for ad in ads[:10]:
        title = ad.get('title', 'Без названия')[:40]
        link = ad.get('link', '')
        if link:
            keyboard.append([InlineKeyboardButton(f"📱 {title}", url=link)])
    
    if not keyboard:
        keyboard.append([InlineKeyboardButton("Объявлений пока нет", callback_data='no_ads')])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data='back_to_main')])
    return InlineKeyboardMarkup(keyboard)

def clean_response(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return text.strip()

# ================= ИИ-АССИСТЕНТ =================
async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not API_KEY:
        await update.message.reply_text("❌ ИИ не настроен. Добавь API ключ в Railway.")
        return
    
    if user_id not in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": "Ты полезный ИИ-ассистент. Отвечай кратко и по делу на русском языке."}
        ]
    
    user_message = update.message.text
    user_histories[user_id].append({"role": "user", "content": user_message})
    
    thinking_msg = await update.message.reply_text("🤔 Думаю...")
    
    try:
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        messages = [{"role": msg["role"], "content": msg["content"]} for msg in user_histories[user_id][-MAX_HISTORY:]]
        
        data = {"model": MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 1024}
        response = requests.post(API_URL, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            ai_response = clean_response(result['choices'][0]['message']['content'])
            user_histories[user_id].append({"role": "assistant", "content": ai_response})
            
            await thinking_msg.delete()
            await update.message.reply_text(ai_response, parse_mode=ParseMode.MARKDOWN)
            
            if len(user_histories[user_id]) > MAX_HISTORY + 1:
                user_histories[user_id] = [user_histories[user_id][0]] + user_histories[user_id][-MAX_HISTORY:]
        else:
            error_text = response.text
            await thinking_msg.delete()
            await update.message.reply_text(f"❌ Ошибка API ({response.status_code}):\n{error_text[:200]}")
            
    except Exception as e:
        await thinking_msg.delete()
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_histories:
        user_histories[user_id] = [{"role": "system", "content": "Ты полезный ИИ-ассистент. Отвечай кратко и по делу на русском языке."}]
    await update.message.reply_text(" История диалога очищена!", reply_markup=get_main_menu())

# ================= КРЕСТИКИ-НОЛИКИ =================
def check_winner(board):
    wins = [(0,1,2), (3,4,5), (6,7,8), (0,3,6), (1,4,7), (2,5,8), (0,4,8), (2,4,6)]
    for a, b, c in wins:
        if board[a] == board[b] == board[c] and board[a] != ' ':
            return board[a]
    return 'draw' if ' ' not in board else None

def get_ttt_keyboard(board):
    keyboard = []
    for i in range(0, 9, 3):
        row = []
        for j in range(3):
            idx = i + j
            if board[idx] == 'X': symbol = '❌'
            elif board[idx] == 'O': symbol = '⭕'
            else: symbol = '⬜'
            row.append(InlineKeyboardButton(symbol, callback_data=f"ttt_{idx}"))
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔄 Новая игра", callback_data="ttt_restart")])
    return InlineKeyboardMarkup(keyboard)

async def tictactoe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tictactoe_games[user_id] = {'board': [' '] * 9, 'active': True}
    await update.message.reply_text("🎮 **Крестики-нолики!**\n\nТы играешь за ❌. Твой ход!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_ttt_keyboard(tictactoe_games[user_id]['board']))

async def tictactoe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "ttt_restart":
        tictactoe_games[user_id] = {'board': [' '] * 9, 'active': True}
        await query.edit_message_text("🎮 **Крестики-нолики!**\n\nТвой ход (❌)!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_ttt_keyboard(tictactoe_games[user_id]['board']))
        return

    if user_id not in tictactoe_games or not tictactoe_games[user_id]['active']:
        await query.answer("Игра не активна! Начни новую /ttt", show_alert=True)
        return

    idx = int(data.split("_")[1])
    board = tictactoe_games[user_id]['board']
    
    if board[idx] != ' ':
        await query.answer("Клетка занята!", show_alert=True)
        return

    board[idx] = 'X'
    winner = check_winner(board)
    
    if winner:
        tictactoe_games[user_id]['active'] = False
        await query.edit_message_text(f"🎉 **{'ТЫ ПОБЕДИЛ!' if winner == 'X' else 'НИЧЬЯ!'}**", parse_mode=ParseMode.MARKDOWN, reply_markup=get_ttt_keyboard(board))
        return

    empty = [i for i, val in enumerate(board) if val == ' ']
    if empty:
        board[random.choice(empty)] = 'O'
        winner = check_winner(board)
        if winner:
            tictactoe_games[user_id]['active'] = False
            await query.edit_message_text(f"🤖 **{'БОТ ПОБЕДИЛ!' if winner == 'O' else 'НИЧЬЯ!'}**", parse_mode=ParseMode.MARKDOWN, reply_markup=get_ttt_keyboard(board))
            return
    
    await query.edit_message_reply_markup(reply_markup=get_ttt_keyboard(board))

# ================= ОБРАБОТЧИК КНОПОК МЕНЮ =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == 'ai_mode':
        await query.edit_message_text("🤖 **ИИ-помощник активирован!**\n\nПросто напиши мне любой вопрос, и я отвечу!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())
    elif data == 'games_menu':
        await query.edit_message_text(" **Игры**\n\n• Крестики-нолики: /ttt\n• Скоро добавлю новые!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())
    elif data == 'ads_menu':
        ads = load_ads()
        if ads:
            await query.edit_message_text("📋 **Актуальные объявления:**\n\nНажми на товар, чтобы открыть:", parse_mode=ParseMode.MARKDOWN, reply_markup=get_ads_keyboard(ads))
        else:
            await query.edit_message_text("📋 **Объявления**\n\nПока нет активных объявлений. Загляни позже!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())
    elif data == 'back_to_main':
        await query.edit_message_text(" **Главное меню**\n\nВыбери действие:", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())
    elif data == 'no_ads':
        await query.answer("Объявлений пока нет", show_alert=True)
    elif data == 'clear_history':
        user_id = update.effective_user.id
        if user_id in user_histories:
            user_histories[user_id] = [{"role": "system", "content": "Ты полезный ИИ-ассистент."}]
        await query.edit_message_text("🧠 История очищена!", reply_markup=get_main_menu())
    elif data == 'get_time':
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz).strftime("%H:%M:%S")
        date = datetime.now(moscow_tz).strftime("%d.%m.%Y")
        await query.edit_message_text(f"⏰ **Сейчас:** {now}\n📅 **Дата:** {date}", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())
    elif data == 'stats':
        await query.edit_message_text(f"📊 **Статистика:**\n\nПользователей с историей: {len(user_histories)}\nАктивных игр: {sum(1 for g in tictactoe_games.values() if g.get('active', False))}", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())

# ================= АДМИН И ОБЪЯВЛЕНИЯ =================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f" **Панель администратора**\n\nДоступные команды:\n/add_ad - Добавить объявление\n/remove_ad - Удалить объявление\n\nТвой ID: `{user_id}`", parse_mode=ParseMode.MARKDOWN)

async def add_ad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда только для администратора")
        return
    
    text = " ".join(context.args) if context.args else ""
    if '|' not in text:
        await update.message.reply_text("❌ Формат: `/add_ad Название | Ссылка`", parse_mode=ParseMode.MARKDOWN)
        return
    
    parts = text.split('|', 1)
    title = parts[0].strip()
    link = parts[1].strip()
    
    ads = load_ads()
    ads.append({"title": title, "link": link})
    save_ads(ads)
    await update.message.reply_text(f"✅ Объявление добавлено: {title}")

async def remove_ad_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
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
                removed = ads.pop(num - 1)
                save_ads(ads)
                await update.message.reply_text(f"✅ Удалено: {removed['title']}")
            else:
                await update.message.reply_text(f"❌ Номер должен быть от 1 до {len(ads)}")
        except ValueError:
            await update.message.reply_text("❌ Введите номер (например: /remove_ad 1)")
        return
    
    text = "📋 Объявления для удаления:\n\n"
    for i, ad in enumerate(ads):
        text += f"{i+1}. {ad['title']}\n"
    text += "\nНапиши `/remove_ad <номер>` чтобы удалить"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ================= ИНСТРУМЕНТЫ =================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 Список команд:\n\n"
        "🔹 /start - Главное меню\n"
        "🔹 /help - Эта справка\n"
        "🔹 /ttt - Крестики-нолики\n"
        "🔹 /calc <выражение> - Калькулятор\n"
        "🔹 /search <запрос> - Поиск в интернете\n"
        "🔹 /time_cmd - Текущее время\n"
        "🔹 /clear - Очистить историю ИИ\n"
        "🔹 /admin - Панель администратора"
    )
    await update.message.reply_text(help_text)

async def calc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expression = " ".join(context.args)
    if not expression:
        await update.message.reply_text("❌ Введите выражение. Пример: `/calc 2+2*2`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(tools.calculate(expression))

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("❌ Введите запрос. Пример: `/search погода в москве`", parse_mode=ParseMode.MARKDOWN)
        return
    thinking_msg = await update.message.reply_text("🔍 Ищу в интернете...")
    result = tools.search_internet(query)
    await thinking_msg.delete()
    await update.message.reply_text(result)

async def time_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(tools.get_current_time())

# ================= СТАРТ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    first_name = update.effective_user.first_name
    
    await update.message.reply_text(
        f"👋 **Привет, {first_name}!** Я Виктор — ИИ-ассистент.\n\n"
        "Я умею:\n"
        "• 🤖 Отвечать на вопросы (просто напиши мне)\n"
        "• 🎮 Играть в игры (крестики-нолики и другие)\n"
        "• 📋 Показывать объявления\n"
        "• 🧮 Считать математику (/calc)\n"
        "• 🔍 Искать в интернете (/search)\n\n"
        "**Нажми на любую кнопку ниже или просто напиши вопрос!**",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu()
    )

# ================= ЗАПУСК =================
def main():
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не найден!")
        return
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("calc", calc_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("time_cmd", time_cmd))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("ttt", tictactoe_command))
    application.add_handler(CommandHandler("add_ad", add_ad_command))
    application.add_handler(CommandHandler("remove_ad", remove_ad_command))
    
    application.add_handler(CallbackQueryHandler(tictactoe_callback, pattern='^ttt_'))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat))
    
    print("✅ Бот запущен! Ожидаем сообщения...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
    
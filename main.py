import os
import re
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

USE_GROQ = bool(GROQ_API_KEY)
API_URL = "https://api.groq.com/openai/v1/chat/completions" if USE_GROQ else "https://api.openai.com/v1/chat/completions"
API_KEY = GROQ_API_KEY if USE_GROQ else OPENAI_API_KEY
MODEL = "llama-3.3-70b-versatile" if USE_GROQ else "gpt-3.5-turbo"

print(f"🤖 Используем: {'Groq (Llama)' if USE_GROQ else 'OpenAI (GPT)'}")
print(f"🔑 API ключ: {'найден' if API_KEY else 'НЕ НАЙДЕН'}")

# ================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =================
MAX_HISTORY = 20
user_histories = {}
tictactoe_games = {}

# ================= УТИЛИТЫ =================
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
        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        
        messages = []
        for msg in user_histories[user_id][-MAX_HISTORY:]:
            messages.append({
                "role": msg["role"],
                "content": msg["content"]
            })
        
        data = {
            "model": MODEL,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 1024
        }
        
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
            await update.message.reply_text(
                f"❌ Ошибка API ({response.status_code}):\n{error_text[:200]}"
            )
            
    except Exception as e:
        await thinking_msg.delete()
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": "Ты полезный ИИ-ассистент. Отвечай кратко и по делу на русском языке."}
        ]
    await update.message.reply_text("🧠 История диалога очищена!", reply_markup=get_main_menu())

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
            if board[idx] == 'X':
                symbol = '❌'
            elif board[idx] == 'O':
                symbol = '⭕'
            else:
                symbol = '⬜'
            row.append(InlineKeyboardButton(symbol, callback_data=f"ttt_{idx}"))
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔄 Новая игра", callback_data="ttt_restart")])
    return InlineKeyboardMarkup(keyboard)

async def tictactoe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    tictactoe_games[user_id] = {'board': [' '] * 9, 'active': True}
    await update.message.reply_text(
        "🎮 **Крестики-нолики!**\n\nТы играешь за ❌. Твой ход!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_ttt_keyboard(tictactoe_games[user_id]['board'])
    )

async def tictactoe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == "ttt_restart":
        tictactoe_games[user_id] = {'board': [' '] * 9, 'active': True}
        await query.edit_message_text(
            "🎮 **Крестики-нолики!**\n\nТвой ход (❌)!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_ttt_keyboard(tictactoe_games[user_id]['board'])
        )
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
        await query.edit_message_text(
            f"🎉 **{'ТЫ ПОБЕДИЛ!' if winner == 'X' else 'НИЧЬЯ!'}**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_ttt_keyboard(board)
        )
        return

    empty = [i for i, val in enumerate(board) if val == ' ']
    if empty:
        import random
        board[random.choice(empty)] = 'O'
        winner = check_winner(board)
        if winner:
            tictactoe_games[user_id]['active'] = False
            await query.edit_message_text(
                f"🤖 **{'БОТ ПОБЕДИЛ!' if winner == 'O' else 'НИЧЬЯ!'}**",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_ttt_keyboard(board)
            )
            return
    
    await query.edit_message_reply_markup(reply_markup=get_ttt_keyboard(board))

# ================= ОБРАБОТЧИК КНОПОК МЕНЮ =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == 'ai_mode':
        await query.edit_message_text(" **ИИ-помощник активирован!**\n\nПросто напиши мне любой вопрос, и я отвечу!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())
    elif data == 'games_menu':
        await query.edit_message_text("🎮 **Игры**\n\n• Крестики-нолики: /ttt\n• Скоро добавлю новые!", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())
    elif data == 'ads_menu':
        await query.edit_message_text("📋 **Объявления**\n\nРаздел в разработке... 🛠", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())
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

# ================= КОМАНДА /admin =================
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"🔧 **Панель администратора**\n\nДоступные команды:\n/stats - Статистика бота\n/broadcast - Рассылка (в разработке)\n/users - Список пользователей (в разработке)\n\nТвой ID: `{user_id}`", parse_mode=ParseMode.MARKDOWN)

# ================= СТАРТ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 **Привет! Я Виктор ИИ Ассистент!**\n\nЯ умею:\n• 🤖 Отвечать на вопросы (ИИ)\n•  Играть в крестики-нолики\n• 📋 Вести объявления (скоро)\n• 🧠 Помнить историю диалога\n\nВыбери действие:", parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_menu())

# ================= НОВЫЕ ИНСТРУМЕНТЫ =================
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        " Список команд:\n\n"
        " /start - Главное меню\n"
        "🔹 /help - Эта справка\n"
        "🔹 /ttt - Играть в крестики-нолики\n"
        "🔹 /calc <выражение> - Калькулятор (пример: /calc 25*4)\n"
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

# ================= ЗАПУСК =================
def main():
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не найден!")
        return
    
    print(" Запускаем бота...")
    print(f"📱 Токен: {TELEGRAM_TOKEN[:10]}...")
    
    if not API_KEY:
        print("⚠️ WARNING: Ни OPENAI_API_KEY, ни GROQ_API_KEY не найдены!")
        print("   ИИ-функции не будут работать. Добавь ключи в Railway.")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("calc", calc_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("time_cmd", time_cmd))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("ttt", tictactoe_command))
    application.add_handler(CommandHandler("stats", lambda u, c: button_handler(u, c)))
    
    # Обработчики кнопок
    application.add_handler(CallbackQueryHandler(tictactoe_callback, pattern='^ttt_'))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Текстовые сообщения
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat))
    
    print("✅ Бот запущен! Ожидаем сообщения...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
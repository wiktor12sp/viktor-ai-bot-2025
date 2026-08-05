import os
import re
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ConversationHandler

# Загружаем переменные окружения
load_dotenv()

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Используем Groq если есть ключ, иначе OpenAI
USE_GROQ = bool(GROQ_API_KEY)
API_URL = "https://api.groq.com/openai/v1/chat/completions" if USE_GROQ else "https://api.openai.com/v1/chat/completions"
API_KEY = GROQ_API_KEY if USE_GROQ else OPENAI_API_KEY
MODEL = "llama-3.1-70b-versatile" if USE_GROQ else "gpt-3.5-turbo"

print(f" Используем: {'Groq (Llama)' if USE_GROQ else 'OpenAI (GPT)'}")

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
         InlineKeyboardButton("📊 Статистика", callback_data='stats')]
    ]
    return InlineKeyboardMarkup(keyboard)

def clean_response(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return text.strip()

# ================= ИИ-АССИСТЕНТ =================
async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id not in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": "Ты полезный ИИ-ассистент. Отвечай кратко и по делу на русском языке."}
        ]
    
    user_message = update.message.text
    
    # Добавляем сообщение пользователя
    user_histories[user_id].append({"role": "user", "content": user_message})
    
    # Отправляем "думаю..."
    thinking_msg = await update.message.reply_text("🤔 Думаю...")
    
    try:
        headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
        data = {
            "model": MODEL,
            "messages": user_histories[user_id][-MAX_HISTORY:]
        }
        
        response = requests.post(API_URL, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            ai_response = clean_response(response.json()['choices'][0]['message']['content'])
            user_histories[user_id].append({"role": "assistant", "content": ai_response})
            
            await thinking_msg.delete()
            await update.message.reply_text(ai_response, parse_mode=ParseMode.MARKDOWN)
            
            # Ограничиваем историю
            if len(user_histories[user_id]) > MAX_HISTORY + 1:
                user_histories[user_id] = [user_histories[user_id][0]] + user_histories[user_id][-MAX_HISTORY:]
        else:
            await thinking_msg.delete()
            await update.message.reply_text(f"❌ Ошибка API: {response.status_code}")
            
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
            symbol = '❌' if board[idx] == 'X' else ('⭕' if board[idx] == 'O' else '⬜')
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

    # Ход бота
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

# ================= ОБРАБОТЧИК КНОПОК =================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'ai_mode':
        await query.edit_message_text(
            " **ИИ-помощник активирован!**\n\nПросто напиши мне любой вопрос, и я отвечу!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu()
        )
    elif query.data == 'games_menu':
        await query.edit_message_text(
            "🎮 **Игры**\n\n• Крестики-нолики: /ttt\n• Скоро добавлю новые!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu()
        )
    elif query.data == 'ads_menu':
        await query.edit_message_text(
            "📋 **Объявления**\n\nРаздел в разработке... 🛠",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu()
        )
    elif query.data == 'clear_history':
        user_id = update.effective_user.id
        if user_id in user_histories:
            user_histories[user_id] = [{"role": "system", "content": "Ты полезный ИИ-ассистент."}]
        await query.edit_message_text(
            "🧠 История очищена!",
            reply_markup=get_main_menu()
        )
    elif query.data == 'get_time':
        from datetime import datetime
        now = datetime.now().strftime("%H:%M:%S")
        await query.edit_message_text(f"⏰ Сейчас: {now}", reply_markup=get_main_menu())
    elif query.data == 'stats':
        await query.edit_message_text(
            f"📊 **Статистика:**\n\n"
            f"Пользователей с историей: {len(user_histories)}\n"
            f"Активных игр: {sum(1 for g in tictactoe_games.values() if g.get('active', False))}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_main_menu()
        )

# ================= СТАРТ =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        " **Привет! Я Виктор ИИ Ассистент!**\n\n"
        "Я умею:\n"
        "• 🤖 Отвечать на вопросы (ИИ)\n"
        "•  Играть в крестики-нолики\n"
        "• 📋 Вести объявления (скоро)\n"
        "• 🧠 Помнить историю диалога\n\n"
        "Выбери действие:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_main_menu()
    )

# ================= ЗАПУСК =================
def main():
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не найден!")
        return
    
    print("🚀 Запускаем бота...")
    print(f"📱 Токен: {TELEGRAM_TOKEN[:10]}...")
    
    if not API_KEY:
        print("⚠️ WARNING: Ни OPENAI_API_KEY, ни GROQ_API_KEY не найдены!")
        print("   ИИ-функции не будут работать. Добавь ключи в .env или на Railway.")
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("ttt", tictactoe_command))
    application.add_handler(CallbackQueryHandler(tictactoe_callback, pattern='^ttt_'))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # ИИ-чат (все текстовые сообщения)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat))
    
    print("✅ Бот запущен! Ожидаем сообщения...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
    
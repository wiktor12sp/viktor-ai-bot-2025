import os
import re
import json
import random
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from datetime import datetime, timedelta
import pytz

# Безопасный импорт tools
try:
    import tools
except ImportError:
    print("⚠️ tools.py не найден, функции калькулятора и поиска будут недоступны")
    tools = None

# Загружаем переменные окружения
load_dotenv()

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8785270105"))

ADS_FILE = "ads_data.json"

USE_GROQ = bool(GROQ_API_KEY)
API_URL = "https://api.groq.com/openai/v1/chat/completions" if USE_GROQ else "https://api.openai.com/v1/chat/completions"
API_KEY = GROQ_API_KEY if USE_GROQ else OPENAI_API_KEY
MODEL = "llama-3.3-70b-versatile" if USE_GROQ else "gpt-3.5-turbo"

print(f"🤖 Используем: {'Groq (Llama)' if USE_GROQ else 'OpenAI (GPT)'}")
print(f"🔑 API ключ: {'найден' if API_KEY else 'НЕ НАЙДЕН'}")

# ================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =================
MAX_HISTORY = 20
HISTORY_TIMEOUT = timedelta(hours=1)  # Очистка истории через час бездействия
user_histories = {}
user_last_activity = {}
tictactoe_games = {}
guess_games = {}
quiz_games = {}

# Исправленный фильтр
class GuessGameFilter(filters.UpdateFilter):
    def filter(self, update):
        return update.effective_user.id in guess_games

# ================= УТИЛИТЫ =================
def clean_old_histories():
    """Очистка старых историй"""
    now = datetime.now()
    to_delete = []
    for user_id, last_active in user_last_activity.items():
        if now - last_active > HISTORY_TIMEOUT:
            to_delete.append(user_id)
    for user_id in to_delete:
        user_histories.pop(user_id, None)
        user_last_activity.pop(user_id, None)
        tictactoe_games.pop(user_id, None)
        guess_games.pop(user_id, None)
        quiz_games.pop(user_id, None)
    if to_delete:
        print(f"🧹 Очищены данные {len(to_delete)} неактивных пользователей")

# [Остальной код функций load_ads, save_ads, get_main_menu и т.д. остается без изменений]

# ================= ИСПРАВЛЕННЫЕ ОБРАБОТЧИКИ =================

async def ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_last_activity[user_id] = datetime.now()  # Обновляем время активности
    
    if not API_KEY:
        await update.message.reply_text("❌ ИИ не настроен.")
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
        messages = [{"role": m["role"], "content": m["content"]} for m in user_histories[user_id][-MAX_HISTORY:]]
        data = {"model": MODEL, "messages": messages, "temperature": 0.7, "max_tokens": 1024}
        
        response = requests.post(API_URL, headers=headers, json=data, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            ai_response = clean_response(result['choices'][0]['message']['content'])
            user_histories[user_id].append({"role": "assistant", "content": ai_response})
            
            await thinking_msg.delete()
            
            # Разбиваем длинные сообщения
            if len(ai_response) > 4000:
                for i in range(0, len(ai_response), 4000):
                    await update.message.reply_text(ai_response[i:i+4000])
            else:
                await update.message.reply_text(ai_response)
            
            if len(user_histories[user_id]) > MAX_HISTORY + 1:
                user_histories[user_id] = [user_histories[user_id][0]] + user_histories[user_id][-MAX_HISTORY:]
        else:
            await thinking_msg.delete()
            await update.message.reply_text(f"❌ Ошибка API ({response.status_code})")
            
    except requests.exceptions.Timeout:
        await thinking_msg.delete()
        await update.message.reply_text("⏰ API не отвечает. Попробуй позже.")
    except Exception as e:
        await thinking_msg.delete()
        await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")

async def tictactoe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        return  # Callback устарел
    
    user_id = query.from_user.id
    data = query.data
    
    try:
        if data == "ttt_restart":
            tictactoe_games[user_id] = {'board': [' ']*9, 'active': True}
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
                f"🎉 **{'ТЫ ПОБЕДИЛ!' if winner=='X' else 'НИЧЬЯ!'}**",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=get_ttt_keyboard(board)
            )
            return
        
        empty = [i for i,v in enumerate(board) if v==' ']
        if empty:
            board[random.choice(empty)] = 'O'
            winner = check_winner(board)
            if winner:
                tictactoe_games[user_id]['active'] = False
                await query.edit_message_text(
                    f"🤖 **{'БОТ ПОБЕДИЛ!' if winner=='O' else 'НИЧЬЯ!'}**",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=get_ttt_keyboard(board)
                )
                return
        
        await query.edit_message_reply_markup(reply_markup=get_ttt_keyboard(board))
        
    except Exception as e:
        print(f"Ошибка в tictactoe_callback: {e}")

# ================= ЗАПУСК =================
def main():
    if not TELEGRAM_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN не найден!")
        return
    
    print("🚀 Запускаем бота...")
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Добавляем задачу очистки старых данных
    application.job_queue.run_repeating(
        lambda context: clean_old_histories(),
        interval=600,  # Каждые 10 минут
        first=10
    )
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    
    if tools:
        application.add_handler(CommandHandler("calc", calc_command))
        application.add_handler(CommandHandler("search", search_command))
    
    application.add_handler(CommandHandler("time_cmd", time_cmd))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("add_ad", add_ad_command))
    application.add_handler(CommandHandler("remove_ad", remove_ad_command))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(CommandHandler("ttt", tictactoe_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("guess", guess_command))
    application.add_handler(CommandHandler("rps", rps_command))
    application.add_handler(CommandHandler("quiz", quiz_command))
    
    # Обработчики кнопок
    application.add_handler(CallbackQueryHandler(tictactoe_callback, pattern='^ttt_'))
    application.add_handler(CallbackQueryHandler(rps_callback, pattern='^rps_'))
    application.add_handler(CallbackQueryHandler(quiz_callback, pattern='^quiz_'))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Текстовые сообщения
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & GuessGameFilter(), guess_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat))
    
    print("✅ Бот запущен! Ожидаем сообщения...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
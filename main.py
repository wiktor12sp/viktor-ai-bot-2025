import os
import re
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

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

print(f" Используем: {'Groq (Llama)' if USE_GROQ else 'OpenAI (GPT)'}")
print(f"🔑 API ключ: {'найден' if API_KEY else 'НЕ НАЙДЕН'}")

# ================= ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =================
MAX_HISTORY = 20
user_histories = {}
tictactoe_games = {}

# ================= УТИЛИТЫ =================
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton(" ИИ-помощник", callback_data='ai_mode'),
         InlineKeyboardButton("🎮 Игры", callback_data='games_menu')],
        [InlineKeyboardButton("📋 Объявления", callback_data='ads_menu'),
         InlineKeyboardButton(" Очистить память", callback_data='clear_history')],
        [InlineKeyboardButton(" Время", callback_data='get_time'),
         InlineKeyboardButton("📊 Статистика", callback_data='stats')]
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
        
        # Формируем сообщения для API
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
        
        print(f"📤 Отправляем запрос к {API_URL}")
        print(f"📝 Модель: {MODEL}")
        print(f"📊 Количество сообщений: {len(messages)}")
        
        response = requests.post(API_URL, headers=headers, json=data, timeout=30)
        
        print(f"📥 Ответ от API: {response.status_code}")
        
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
            print(f" Ошибка API: {response.status_code}")
            print(f"📄 Текст ошибки: {error_text}")
            
            await thinking_msg.delete()
            await update.message.reply_text(
                f"❌ Ошибка API ({response.status_code}):\n{error_text[:200]}"
            )
            
    except Exception as e:
        await thinking_msg.delete()
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
        print(f"💥 Исключение: {e}")

async def clear_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_histories:
        user_histories[user_id] = [
            {"role": "system", "content": "Ты полезный ИИ-ассистент. Отвечай кратко и по делу на русском языке."}
        ]
    await update.message.reply_text(" История диалога очищена!", reply_markup=get_main_menu())

# ================= КРЕСТИКИ-НОЛИКИ =================
def check_winner(board):
    wins = [(0,1,2), (3,4,5), (6,7,8), (0,3,6), (1,4,7), (2,5,8), (0,4,8), (2,4,6)]
    for a, b, c in wins:
        if board[a
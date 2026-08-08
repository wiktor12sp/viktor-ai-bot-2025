import requests
import re
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    import pytz as ZoneInfo

def search_internet(query: str) -> str:
    """🔍 Поиск в интернете через DuckDuckGo (бесплатно)"""
    try:
        url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(query)}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            results = soup.find_all('a', class_='result__snippet')
            
            if results:
                text = f"🔍 Результаты по запросу '{query}':\n\n"
                for i, result in enumerate(results[:5], 1):
                    text += f"{i}. {result.text.strip()}\n"
                return text
            return f"🔍 По запросу '{query}' ничего не найдено."
        return f"❌ Ошибка поиска: {response.status_code}"
    except Exception as e:
        return f"❌ Ошибка поиска: {str(e)}"

def calculate(expression: str) -> str:
    """🧮 Калькулятор"""
    try:
        # Разрешаем только цифры и мат. знаки для безопасности
        if not re.match(r'^[\d\s\+\-\*\/\(\)\.\%]+$', expression):
            return "❌ Недопустимые символы. Используй только цифры и + - * / ( )"
        result = eval(expression)
        return f"🧮 {expression} = {result}"
    except Exception as e:
        return f"❌ Ошибка вычисления: {str(e)}"

def get_current_time() -> str:
    """📅 Текущее время (Москва)"""
    try:
        tz = ZoneInfo("Europe/Moscow")
        now = datetime.now(tz)
        return f"📅 Сейчас: {now.strftime('%d.%m.%Y %H:%M:%S')} (МСК)"
    except Exception:
        now = datetime.now()
        return f"📅 Сейчас: {now.strftime('%d.%m.%Y %H:%M:%S')}"
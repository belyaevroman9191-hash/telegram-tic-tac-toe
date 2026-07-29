import os
import sqlite3

os.environ["WEB_CONCURRENCY"] = "1"

TOKEN = os.getenv("BOT_TOKEN", "8756387431:AAHVVg2yXaHFC_XngwuwAkODLz7yUEQY2XA")
SERVER_URL = os.getenv("SERVER_URL", "https://telegram-tic-tac-toe-8dv1.onrender.com")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "SUPER_SECRET_WINS_KEY_99")

db_path = os.path.join(os.path.dirname(__file__), "tic_tac_toe.db")
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()

# ДОБАВЛЕНО ПОЛЕ draws СЮДА
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    draws INTEGER DEFAULT 0,
    current_skin TEXT DEFAULT 'classic',
    unlocked_skins TEXT DEFAULT 'classic'
)
""")
conn.commit()

SKINS_CONFIG = {
    "classic": {"name": "Классика", "cost": 0, "x": "❌", "o": "⭕"},
    "ninja": {"name": "Дуэль Ниндзя", "cost": 3, "x": "⚔️", "o": "🛡️"},
    "elements": {"name": "Магия стихий", "cost": 5, "x": "🔥", "o": "💧"},
    "halloween": {"name": "Хэллоуин", "cost": 10, "x": "🎃", "o": "💀"},
    "space": {"name": "Космос", "cost": 15, "x": "🚀", "o": "🛸"}
}

game_rooms = {}

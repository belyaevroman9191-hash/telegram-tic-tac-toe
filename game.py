import os
import sys
import time
import asyncio
import logging
import sqlite3
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
import uvicorn

# Запрещаем Render создавать дублирующие процессы бота при старте
os.environ["WEB_CONCURRENCY"] = "1"

# Безопасное чтение переменных окружения из панели управления Render
TOKEN = os.getenv("BOT_TOKEN", "8756387431:AAHVVg2yXaHFC_XngwuwAkODLz7yUEQY2XA")
SERVER_URL = os.getenv("SERVER_URL", "https://telegram-tic-tac-toe-8dv1.onrender.com")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "SUPER_SECRET_WINS_KEY_99")

# Настройка и инициализация базы данных SQLite
db_path = os.path.join(os.path.dirname(__file__), "tic_tac_toe.db")
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    current_skin TEXT DEFAULT 'classic',
    unlocked_skins TEXT DEFAULT 'classic'
)
""")
conn.commit()

# Динамически добавляем колонку draws (ничьи), если её нет в таблице
try:
    cursor.execute("ALTER TABLE users ADD COLUMN draws INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

# Конфигурация внутриигрового магазина скинов
SKINS_CONFIG = {
    "classic": {"name": "Классика", "cost": 0, "x": "❌", "o": "⭕"},
    "ninja": {"name": "Дуэль Ниндзя", "cost": 3, "x": "⚔️", "o": "🛡️"},
    "elements": {"name": "Магия стихий", "cost": 5, "x": "🔥", "o": "💧"},
    "halloween": {"name": "Хэллоуин", "cost": 10, "x": "🎃", "o": "💀"},
    "space": {"name": "Космос", "cost": 15, "x": "🚀", "o": "🛸"}
}

game_rooms = {}
def check_server_win(b, s):
    win_patterns = [[0, 1, 2], [3, 4, 5], [6, 7, 8], [0, 3, 6], [1, 4, 7], [2, 5, 8], [0, 4, 8], [2, 4, 6]]
    for p in win_patterns:
        if b[p[0]] == s and b[p[1]] == s and b[p[2]] == s:
            return p
    return None

# Настройка FastAPI сервера
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Настройка aiogram бота
bot = Bot(token=TOKEN)
dp = Dispatcher()

@app.get("/")
@app.get("/game")
async def get_game():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api/state/{room_id}/{user_id}")
async def get_state(room_id: str, user_id: str):
    current_time = time.time()
    user_id = str(user_id)
    room_id = str(room_id)
    
    if room_id not in game_rooms:
        game_rooms[room_id] = {
            "board": [""] * 9, "player1": user_id, "player2": None,
            "status": "wait", "winner": "", "win_line": [], "turn": "X",
            "rematch_requests": [], "rematch_declined": False, "last_seen": {}
        }

    room = game_rooms[room_id]
    # Если комната покинута и заходит сам создатель комнаты, сбрасываем её
    if room.get("status") == "left" and room.get("player1") == user_id:
        game_rooms[room_id] = {
            "board": [""] * 9, "player1": user_id, "player2": None,
            "status": "wait", "winner": "", "win_line": [], "turn": "X",
            "rematch_requests": [], "rematch_declined": False, "last_seen": {}
        }
        room = game_rooms[room_id]
    if "last_seen" not in room:
        room["last_seen"] = {}
    room["last_seen"][user_id] = current_time
    
    if room["player1"] != user_id:
        if room["player2"] is None:
            room["player2"] = user_id
            room["status"] = "active"
            room["turn_start_time"] = time.time()
            
    if room["player1"] and room["player2"] and room["status"] == "wait":
        room["status"] = "active"
        room["turn_start_time"] = time.time()
    if room["status"] == "active":
        if "turn_start_time" not in room:
            room["turn_start_time"] = time.time()
        elapsed_time = current_time - room["turn_start_time"]
        if elapsed_time > 10.0:
            room["status"] = "over"
            room["winner"] = "O" if room["turn"] == "X" else "X"
            room["win_line"] = []
            p1_id = int(room["player1"]) if room["player1"].isdigit() else 0
            p2_id = int(room["player2"]) if room["player2"].isdigit() else 0
            if room["winner"] == "X" and p1_id and p2_id:
                cursor.execute("UPDATE users SET wins = IFNULL(wins, 0) + 1 WHERE user_id = ?", (p1_id,))
                cursor.execute("UPDATE users SET losses = IFNULL(losses, 0) + 1 WHERE user_id = ?", (p2_id,))
            elif room["winner"] == "O" and p1_id and p2_id:
                cursor.execute("UPDATE users SET wins = IFNULL(wins, 0) + 1 WHERE user_id = ?", (p2_id,))
                cursor.execute("UPDATE users SET losses = IFNULL(losses, 0) + 1 WHERE user_id = ?", (p1_id,))
            conn.commit()
        room_timeout_left = max(0, 10 - int(elapsed_time))
    else:
        room_timeout_left = 10

    p1, p2 = str(room["player1"]), str(room["player2"])
    opponent_id = p2 if user_id == p1 else p1
    if opponent_id and opponent_id != "None" and room["status"] in ["active", "over"]:
        if current_time - room["last_seen"].get(opponent_id, 0) > 6.0:
            room["status"] = "left"

    try:
        cursor.execute("SELECT IFNULL(wins, 0), IFNULL(losses, 0), IFNULL(draws, 0), current_skin, unlocked_skins FROM users WHERE user_id = ?", (int(user_id) if user_id.isdigit() else 0,))
        user_stats = cursor.fetchone()
        wins, losses, draws, current_skin, unlocked_skins = user_stats if user_stats else (0, 0, 0, 'classic', 'classic')
    except Exception:
        wins, losses, draws, current_skin, unlocked_skins = 0, 0, 0, 'classic', 'classic'
    
    cursor.execute("SELECT current_skin FROM users WHERE user_id = ?", (int(room["player1"]) if room["player1"].isdigit() else 0,))
    p1_skin_row = cursor.fetchone()
    p1_skin = p1_skin_row if p1_skin_row else "classic"
    
    p2_skin = "classic"
    if room["player2"]:
        cursor.execute("SELECT current_skin FROM users WHERE user_id = ?", (int(room["player2"]) if room["player2"].isdigit() else 0,))
        p2_skin_row = cursor.fetchone()
        p2_skin = p2_skin_row if p2_skin_row else "classic"
        
    visual_board = []
    for cell in room["board"]:
        if cell == "X":
            visual_board.append(SKINS_CONFIG.get(p1_skin, SKINS_CONFIG["classic"])["x"])
        elif cell == "O":
            visual_board.append(SKINS_CONFIG.get(p2_skin, SKINS_CONFIG["classic"])["o"])
        else:
            visual_board.append("")
            
    my_symbol = "X" if room["player1"] == user_id else "O"
    my_visual_symbol = SKINS_CONFIG.get(p1_skin if my_symbol == "X" else p2_skin, SKINS_CONFIG["classic"])["x" if my_symbol == "X" else "o"]
    
    return {
        "board": visual_board, "status": room["status"], "symbol": my_symbol,
        "visual_symbol": my_visual_symbol, "turn": room["turn"], "winner": room["winner"], 
        "win_line": room.get("win_line", []), "user_wins": int(wins), "user_losses": int(losses), "user_draws": int(draws),
        "current_skin": current_skin, "unlocked_skins": unlocked_skins,
        "rematch_requests": room.get("rematch_requests", []), "rematch_declined": room.get("rematch_declined", False),
        "time_left": room_timeout_left
    }
@app.post("/api/move/{room_id}")
async def make_move(room_id: str, data: dict = Body(...)):
    if room_id not in game_rooms: raise HTTPException(status_code=404, detail="Room not found")
    room = game_rooms[room_id]
    index, symbol, user_id = data.get("index"), data.get("symbol"), data.get("user_id")
    if "last_seen" in room and user_id: room["last_seen"][str(user_id)] = time.time()
    if room["board"][index] != "" or room["turn"] != symbol or room["status"] != "active": return {"success": False}
    
    room["board"][index] = symbol
    room["turn"] = "O" if symbol == "X" else "X"
    room["turn_start_time"] = time.time()
    
    winning_pattern = check_server_win(room["board"], symbol)
    if winning_pattern:
        room["status"] = "over"
        room["winner"] = symbol
        room["win_line"] = winning_pattern
        winner_id = int(user_id)
        loser_id = int(room["player2"]) if str(winner_id) == str(room["player1"]) else int(room["player1"])
        cursor.execute("UPDATE users SET wins = IFNULL(wins, 0) + 1 WHERE user_id = ?", (winner_id,))
        cursor.execute("UPDATE users SET losses = IFNULL(losses, 0) + 1 WHERE user_id = ?", (loser_id,))
        conn.commit()
    elif "" not in room["board"]:
        room["status"] = "over"
        room["winner"] = "Ничья"
        room["win_line"] = []
        p1_id = int(room["player1"]) if room["player1"].isdigit() else 0
        p2_id = int(room["player2"]) if room["player2"].isdigit() else 0
        if p1_id: cursor.execute("UPDATE users SET draws = IFNULL(draws, 0) + 1 WHERE user_id = ?", (p1_id,))
        if p2_id: cursor.execute("UPDATE users SET draws = IFNULL(draws, 0) + 1 WHERE user_id = ?", (p2_id,))
        conn.commit()
    return {"success": True}

@app.post("/api/shop/{user_id}")
async def handle_shop(user_id: str, data: dict = Body(...)):
    user_id = int(user_id) if user_id.isdigit() else 0
    action = data.get("action")
    skin_id = data.get("skin_id") or data.get("skinId") # Защита от camelCase/snake_case
    
    cursor.execute("SELECT wins, unlocked_skins FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row: 
        raise HTTPException(status_code=404, detail="User not found")
        
    # --- ОШИБКА БЫЛА ЗДЕСЬ (строка ниже исправлена) ---
    wins, unlocked_skins = row[0], row[1] if row[1] else "classic"
    unlocked_list = unlocked_skins.split(",")
    
    if action == "equip":
        if skin_id in unlocked_list:
            cursor.execute("UPDATE users SET current_skin = ? WHERE user_id = ?", (skin_id, user_id))
            conn.commit()
            return {"success": True, "message": "Скин успешно экипирован"}
        return {"success": False, "message": "Скин еще не куплен"}
        
    elif action == "buy":
        if skin_id in unlocked_list: 
            return {"success": False, "message": "Скин уже куплен"}
            
        cost = 3 if skin_id == "ninja" else (5 if skin_id == "elements" else (10 if skin_id == "halloween" else 15))
        if wins >= cost:
            new_wins = wins - cost
            unlocked_list.append(skin_id)
            new_unlocked = ",".join(unlocked_list)
            cursor.execute("UPDATE users SET wins = ?, unlocked_skins = ?, current_skin = ? WHERE user_id = ?", (new_wins, new_unlocked, skin_id, user_id))
            conn.commit()
            return {"success": True, "message": "Скин успешно куплен!"}
        return {"success": False, "message": "Недостаточно побед для покупки"}

@app.post("/api/rematch/{room_id}")
async def handle_rematch(room_id: str, data: dict = Body(...)):
    if room_id not in game_rooms: raise HTTPException(status_code=404, detail="Room not found")
    room = game_rooms[room_id]
    action, user_id = data.get("action"), data.get("user_id")
    if "last_seen" in room and user_id: room["last_seen"][str(user_id)] = time.time()
    p1, p2 = room["player1"], room["player2"]
    opponent_id = p2 if str(user_id) == str(p1) else p1
    
    if action == "request":
        if str(opponent_id) in [str(uid) for uid in room["rematch_requests"]]: action = "accept"
        else:
            if user_id not in room["rematch_requests"]: room["rematch_requests"].append(user_id)
            room["rematch_declined"] = False
            return {"success": True}
            
    if action == "accept":
        room["board"], room["status"], room["winner"], room["win_line"], room["rematch_requests"], room["rematch_declined"] = [""] * 9, "active", "", [], [], False
        room["player1"], room["player2"], room["turn"] = p2, p1, "X"
        room["turn_start_time"] = time.time()
    elif action == "decline":
        room["rematch_declined"], room["rematch_requests"] = True, []
    return {"success": True}
@app.post("/api/admin/give")
async def admin_give_wins(data: dict = Body(...)):
    if data.get("secret_password") != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Access Denied")
    target_user_id = data.get("target_id")
    amount = data.get("amount", "0")
    if not target_user_id: raise HTTPException(status_code=400, detail="Missing user ID")
    try:
        target_user_id = int(target_user_id)
        cursor.execute("SELECT wins FROM users WHERE user_id = ?", (target_user_id,))
        if not cursor.fetchone(): return {"success": False, "message": "Игрок не найден в БД!"}
        
        if str(amount).lower() == "top":
            cursor.execute("UPDATE users SET wins = 500, losses = 0, draws = 0 WHERE user_id = ?", (target_user_id,))
            conn.commit()
            return {"success": True, "message": "👑 Игрок переведен в ТОП-1!"}
            
        val = int(amount)
        cursor.execute("UPDATE users SET wins = IFNULL(wins, 0) + ? WHERE user_id = ?", (val, target_user_id))
        conn.commit()
        return {"success": True, "message": f"Начислено {val} побед!"}
    except Exception as e:
        return {"success": False, "message": f"Ошибка: {str(e)}"}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    
    text_parts = message.text.split()
    if len(text_parts) > 1 and text_parts[1].startswith("game_"):
        room_id = text_parts[1].replace("game_", "")
        link = f"{SERVER_URL}/game?room={room_id}&user={user_id}"
        await message.answer(
            "⚔️ Вы приняли вызов! Нажмите кнопку ниже, чтобы войти в игру:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="Играть 🎮", web_app=types.WebAppInfo(url=link))]
            ])
        )
        return

    link = f"{SERVER_URL}/game?room={user_id}&user={user_id}"
    markup = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🎮 Войти в свою комнату", web_app=types.WebAppInfo(url=link))],
            [types.KeyboardButton(text="🏆 Таблица лидеров"), types.KeyboardButton(text="🤝 Позвать друга")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )
    await message.answer("❌⭕ Добро пожаловать! Используйте меню ниже для управления игрой.", reply_markup=markup)

@dp.message(lambda msg: msg.text == "🏆 Таблица лидеров")
@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    try:
        cursor.execute("SELECT username, IFNULL(wins, 0), IFNULL(losses, 0), IFNULL(draws, 0) FROM users ORDER BY wins DESC, losses ASC LIMIT 10")
        leaders = cursor.fetchall()
        if not leaders:
            await message.answer("Таблица лидеров пока пуста! 🏆")
            return
        text = "🏆 **ТОП-10 ИГРОКОВ:**\n\n"
        for i, (username, wins, losses, draws) in enumerate(leaders, 1):
            total_games = wins + losses + draws
            winrate = round((wins / total_games) * 100) if total_games > 0 else 0
            text += f"{i}. @{username} — {wins} 🥇 / {losses} ❌ / {draws} 🤝 | 📈 WR: {winrate}%\n"
        await message.answer(text, parse_mode="Markdown")
    except Exception:
        await message.answer("Произошла ошибка при загрузке топа.")

@dp.message(lambda msg: msg.text == "🤝 Позвать друга")
async def cmd_invite_link(message: types.Message):
    bot_info = await bot.get_me()
    deep_link = f"https://t.me/{bot_info.username}?start=game_{message.from_user.id}"
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Вызвать друга на дуэль ⚔️", switch_inline_query=f"Вызываю тебя на дуэль! Переходи по ссылке: {deep_link}")]
    ])
    await message.answer("Нажмите на кнопку ниже, выберите друга, и приглашение отправится ему в чат:", reply_markup=markup)

async def main():
    bot_task = asyncio.create_task(dp.start_polling(bot))
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info", workers=1, loop="asyncio")
    server = uvicorn.Server(config)
    await server.serve()
    bot_task.cancel()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

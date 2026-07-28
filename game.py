import asyncio
import json
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

TOKEN = "8756387431:AAGFETfMx3WoBCxATBvYWutsuRI9-8VkU_I"
SERVER_URL = "https://telegram-tic-tac-toe-8dv1.onrender.com" 

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

db_path = os.path.join(os.path.dirname(__file__), "tic_tac_toe.db")
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0
)
""")
conn.commit()

# Хранилище комнат в памяти
game_rooms = {}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    
    args = message.text.split()
    if len(args) > 1 and args[1].startswith("game_"):
        room_id = args[1].replace("game_", "")
        link = f"{SERVER_URL}/game?room={room_id}&user={user_id}"
        await message.answer(
            "⚔️ Вы приняли вызов! Нажмите кнопку, чтобы войти в игру:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="Играть", web_app=types.WebAppInfo(url=link))]
            ])
        )
        return

    bot_info = await bot.get_me()
    invite_link = f"https://t.me/{(await bot.get_me()).username}?start=game_{user_id}"
    
    link = f"{SERVER_URL}/game?room={user_id}&user={user_id}"
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Войти в свою комнату", web_app=types.WebAppInfo(url=link))],
        [types.InlineKeyboardButton(text="Отправить ссылку другу", switch_inline_query=f"Вызываю тебя на дуэль! Переходи: {invite_link}")]
    ])
    await message.answer("❌⭕ Добро пожаловать! Создайте комнату или отправьте ссылку другу.", reply_markup=markup)

@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    cursor.execute("SELECT username, wins, losses FROM users ORDER BY wins DESC, losses ASC LIMIT 10")
    leaders = cursor.fetchall()
    
    if not leaders:
        await message.answer("Таблица лидеров пока пуста! 🏆")
        return
        
    text = "🏆 **ТОП-10 ИГРОКОВ:**\n\n"
    for i, (username, wins, losses) in enumerate(leaders, 1):
        text += f"{i}. @{username} — {wins} 🥇 / {losses} 👎\n"
    await message.answer(text, parse_mode="Markdown")

@app.get("/")
@app.get("/game")
async def get_game():
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# --- НАДЕЖНЫЙ HTTP API ДЛЯ ИГРЫ ---

@app.get("/api/state/{room_id}/{user_id}")
async def get_state(room_id: str, user_id: str):
    if room_id not in game_rooms:
        game_rooms[room_id] = {
            "board": [""] * 9,
            "player1": user_id,
            "player2": None,
            "status": "wait",
            "winner": "",
            "turn": "X",
            "rematch_requests": [],  # ДОРАБОТАНО: список ID игроков, запросивших реванш
            "rematch_declined": False # ДОРАБОТАНО: флаг отказа от реванша
        }
    
    room = game_rooms[room_id]
    
    if room["player1"] != user_id and room["player2"] is None:
        room["player2"] = user_id
        room["status"] = "active"
    
    my_symbol = "X" if room["player1"] == user_id else "O"
    
    cursor.execute("SELECT wins, losses FROM users WHERE user_id = ?", (int(user_id) if user_id.isdigit() else 0,))
    user_stats = cursor.fetchone()
    wins, losses = user_stats if user_stats else (0, 0)
    
    return {
        "board": room["board"],
        "status": room["status"],
        "symbol": my_symbol,
        "turn": room["turn"],
        "winner": room["winner"],
        "user_wins": wins,
        "user_losses": losses,
        "rematch_requests": room.get("rematch_requests", []),
        "rematch_declined": room.get("rematch_declined", False)
    }

@app.post("/api/move/{room_id}")
async def make_move(room_id: str, data: dict = Body(...)):
    if room_id not in game_rooms:
        raise HTTPException(status_code=404, detail="Room not found")
        
    room = game_rooms[room_id]
    index = data.get("index")
    symbol = data.get("symbol")
    
    if room["board"][index] != "" or room["turn"] != symbol or room["status"] != "active":
        return {"success": False}
        
    room["board"][index] = symbol
    room["turn"] = "O" if symbol == "X" else "X"
    
    if data.get("game_over") and data.get("winner_id"):
        room["status"] = "over"
        room["winner"] = symbol
        
        winner_id = int(data["winner_id"])
        loser_id = int(room["player2"]) if str(winner_id) == str(room["player1"]) else int(room["player1"])
        
        cursor.execute("UPDATE users SET wins = wins + 1 WHERE user_id = ?", (winner_id,))
        cursor.execute("UPDATE users SET losses = losses + 1 WHERE user_id = ?", (loser_id,))
        conn.commit()
        
    elif "" not in room["board"]:
        room["status"] = "over"
        room["winner"] = "Ничья"
        
    return {"success": True}

# ДОРАБОТАНО: Эндпоинт для обработки реваншей
@app.post("/api/rematch/{room_id}")
async def handle_rematch(room_id: str, data: dict = Body(...)):
    if room_id not in game_rooms:
        raise HTTPException(status_code=404, detail="Room not found")
        
    room = game_rooms[room_id]
    action = data.get("action")  # 'request', 'accept', 'decline'
    user_id = data.get("user_id")
    
    if action == "request":
        if user_id not in room["rematch_requests"]:
            room["rematch_requests"].append(user_id)
            room["rematch_declined"] = False
            
    elif action == "accept":
        # Сброс игрового поля и запуск нового раунда
        room["board"] = [""] * 9
        room["status"] = "active"
        room["winner"] = ""
        room["rematch_requests"] = []
        room["rematch_declined"] = False
        
        # Меняем игроков ролями, чтобы тот, кто ходил вторым, теперь ходил первым
        p1, p2 = room["player1"], room["player2"]
        room["player1"] = p2
        room["player2"] = p1
        room["turn"] = "X"
        
    elif action == "decline":
        room["rematch_declined"] = True
        room["rematch_requests"] = []
        
    return {"success": True}

async def main():
    bot_task = asyncio.create_task(dp.start_polling(bot))
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
    bot_task.cancel()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

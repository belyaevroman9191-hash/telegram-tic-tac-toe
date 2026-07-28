import asyncio
import json
import logging
import sqlite3
import os
import time
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

TOKEN = "8756387431:AAHVVg2yXaHFC_XngwuwAkODLz7yUEQY2XA"
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

game_rooms = {}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    
    args = message.text.split()
    bot_info = await bot.get_me()
    
    # Если зашли по инвайт-ссылке друга
    if len(args) > 1 and args[1].startswith("game_"):
        room_id = args[1].replace("game_", "")
        # Формируем прямую ссылку на WebApp, где четко указаны параметры комнаты и зашедшего игрока
        link = f"{SERVER_URL}/game?room={room_id}&user={user_id}"
        await message.answer(
            "⚔️ Вы приняли вызов! Нажмите кнопку ниже, чтобы войти в игру:",
            reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
                [types.InlineKeyboardButton(text="Играть", web_app=types.WebAppInfo(url=link))]
            ])
        )
        return

    link = f"{SERVER_URL}/game?room={user_id}&user={user_id}"
    markup = types.ReplyKeyboardMarkup(
        keyboard=[
            [types.KeyboardButton(text="🚪 Войти в свою комнату", web_app=types.WebAppInfo(url=link))],
            [types.KeyboardButton(text="🏆 Table лидеров"), types.KeyboardButton(text="🔗 Позвать друга")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )
    await message.answer("❌⭕ Добро пожаловать! Используйте меню ниже для управления игрой.", reply_markup=markup)

@dp.message(lambda msg: msg.text == "🏆 Таблица лидеров")
@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    try:
        cursor.execute("SELECT username, IFNULL(wins, 0), IFNULL(losses, 0) FROM users ORDER BY wins DESC, losses ASC LIMIT 10")
        leaders = cursor.fetchall()
        if not leaders:
            await message.answer("Таблица лидеров пока пуста! 🏆")
            return
        text = "🏆 **ТОП-10 ИГРОКОВ:**\n\n"
        for i, (username, wins, losses) in enumerate(leaders, 1):
            text += f"{i}. @{username} — {wins} 🥇 / {losses} 👎\n"
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        await message.answer("Произошла ошибка при загрузке топа.")

@dp.message(lambda msg: msg.text == "🔗 Позвать друга")
async def cmd_invite_link(message: types.Message):
    bot_info = await bot.get_me()
    # Ссылка-команда глубокого связывания в Telegram, которая не ломается на ПК
    deep_link = f"https://t.me/{bot_info.username}?start=game_{message.from_user.id}"
    
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Вызвать друга на дуэль ⚔️", switch_inline_query=f"Вызываю тебя на дуэль! Переходи по ссылке: {deep_link}")]
    ])
    await message.answer("Нажмите на кнопку ниже, выберите друга, и приглашение отправится ему в чат:", reply_markup=markup)

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
            "board": [""] * 9,
            "player1": user_id,
            "player2": None,
            "status": "wait",
            "winner": "",
            "turn": "X",
            "rematch_requests": [],
            "rematch_declined": False,
            "last_seen": {}
        }
    
    room = game_rooms[room_id]
    if "last_seen" not in room:
        room["last_seen"] = {}
    room["last_seen"][user_id] = current_time
    
    # Четкое распределение по комнатам без ложных wait-состояний
    if room["player1"] != user_id:
        if room["player2"] is None or room["player2"] == user_id:
            room["player2"] = user_id
            room["status"] = "active"
            
    if room["player1"] and room["player2"] and room["status"] == "wait":
        room["status"] = "active"
    
    if room["status"] in ["active", "over"]:
        p1, p2 = str(room["player1"]), str(room["player2"])
        opponent_id = p2 if user_id == p1 else p1
        last_active = room["last_seen"].get(opponent_id, 0)
        if current_time - last_active > 7.0:
            room["status"] = "left"
            room["winner"] = "opponent_left"

    my_symbol = "X" if room["player1"] == user_id else "O"
    cursor.execute("SELECT IFNULL(wins, 0), IFNULL(losses, 0) FROM users WHERE user_id = ?", (int(user_id) if user_id.isdigit() else 0,))
    user_stats = cursor.fetchone()
    wins, losses = user_stats if user_stats else (0, 0)
    
    return {
        "board": room["board"], "status": room["status"], "symbol": my_symbol,
        "turn": room["turn"], "winner": room["winner"], "user_wins": wins, "user_losses": losses,
        "rematch_requests": room.get("rematch_requests", []), "rematch_declined": room.get("rematch_declined", False)
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
    if data.get("game_over") and data.get("winner_id"):
        room["status"] = "over"
        room["winner"] = symbol
        winner_id = int(data["winner_id"])
        loser_id = int(room["player2"]) if str(winner_id) == str(room["player1"]) else int(room["player1"])
        cursor.execute("UPDATE users SET wins = IFNULL(wins, 0) + 1 WHERE user_id = ?", (winner_id,))
        cursor.execute("UPDATE users SET losses = IFNULL(losses, 0) + 1 WHERE user_id = ?", (loser_id,))
        conn.commit()
    elif "" not in room["board"]:
        room["status"] = "over"
        room["winner"] = "Ничья"
    return {"success": True}

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
            if user_id not in room["rematch_requests"]:
                room["rematch_requests"].append(user_id)
                room["rematch_declined"] = False
            return {"success": True}
    if action == "accept":
        room["board"], room["status"], room["winner"], room["rematch_requests"], room["rematch_declined"] = [""] * 9, "active", "", [], False
        room["player1"], room["player2"], room["turn"] = p2, p1, "X"
    elif action == "decline":
        room["rematch_declined"], room["rematch_requests"] = True, []
    return {"success": True}

async def main():
    bot_task = asyncio.create_task(dp.start_polling(bot))
    
    # ИСПРАВЛЕНО: Жестко задаем workers=1 и отключаем цикличный loop-conflict
    config = uvicorn.Config(
        app, 
        host="0.0.0.0", 
        port=8000, 
        log_level="info",
        workers=1,
        loop="asyncio"
    )
    server = uvicorn.Server(config)
    await server.serve()
    bot_task.cancel()

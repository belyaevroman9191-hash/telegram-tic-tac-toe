import asyncio
import json
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

# Токен вашего бота от @BotFather
TOKEN = "8756387431:AAGFETfMx3WoBCxATBvYWutsuRI9-8VkU_I"
# URL вашего сервера на Render
SERVER_URL = "https://telegram-tic-tac-toe-8dv1.onrender.com" 

bot = Bot(token=TOKEN)
dp = Dispatcher()
app = FastAPI()

# Инициализация базы данных SQLite в правильной рабочей директории
db_path = os.path.join(os.path.dirname(__file__), "tic_tac_toe.db")
conn = sqlite3.connect(db_path, check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    wins INTEGER DEFAULT 0
)
""")
conn.commit()

# Хранилище активных WebSocket подключений: {room_id: [websocket1, websocket2]}
rooms = {}

# --- ЛОГИКА ТЕЛЕГРАМ БОТА ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    # Добавляем пользователя или обновляем его имя, если изменилось
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
    conn.commit()
    
    args = message.text.split()
    # Если игрок перешел по ссылке друга (проверяем наличие аргументов)
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

    # Если игрок создает новую игру самостоятельно
    bot_info = await bot.get_me()
    invite_link = f"https://t.me/{bot_info.username}?start=game_{user_id}"
    
    link = f"{SERVER_URL}/game?room={user_id}&user={user_id}"
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Войти в свою комнату", web_app=types.WebAppInfo(url=link))],
        [types.InlineKeyboardButton(text="Отправить ссылку другу", switch_inline_query=f"Вызываю тебя на дуэль! Переходи: {invite_link}")]
    ])
    await message.answer("❌⭕ Добро пожаловать! Создайте комнату или отправьте ссылку другу.", reply_markup=markup)

@dp.message(Command("top"))
async def cmd_top(message: types.Message):
    cursor.execute("SELECT username, wins FROM users ORDER BY wins DESC LIMIT 10")
    leaders = cursor.fetchall()
    
    if not leaders:
        await message.answer("Таблица лидеров пока пуста! 🏆")
        return
        
    text = "🏆 **ТОП-10 ИГРОКОВ:**\n\n"
    for i, (username, wins) in enumerate(leaders, 1):
        text += f"{i}. @{username} — {wins} 🥇\n"
    await message.answer(text, parse_mode="Markdown")

# --- ЛОГИКА ВЕБ-СЕРВЕРА И WEBSOCKETS ---
@app.get("/")
@app.get("/game")
async def get_game():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# --- ЛОГИКА ВЕБ-СЕРВЕРА И WEBSOCKETS ---
@app.get("/game")
async def get_game():
    # Отдает файл index.html, который лежит в той же папке
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    await websocket.accept()
    if room_id not in rooms:
        rooms[room_id] = []
    
    if len(rooms[room_id]) >= 2:
        await websocket.close(code=4000, reason="Комната заполнена")
        return
        
    rooms[room_id].append(websocket)
    
    # Распределяем символы: первый игрок — X, второй — O
    player_symbol = "X" if len(rooms[room_id]) == 1 else "O"
    await websocket.send_json({"type": "init", "symbol": player_symbol})
    
    # Если зашли оба игрока — запускаем матч
    if len(rooms[room_id]) == 2:
        for ws in rooms[room_id]:
            await ws.send_json({"type": "start"})

    try:
        while True:
            data = await websocket.receive_text()
            event = json.loads(data)
            
            # Транслируем действия игроков друг другу
            if event["type"] in ["move", "game_over"]:
                for ws in rooms[room_id]:
                    if ws != websocket:
                        await ws.send_text(data)
                        
            # Запись очка за победу в базу данных SQLite
            if event["type"] == "game_over" and event.get("winner_id"):
                try:
                    cursor.execute("UPDATE users SET wins = wins + 1 WHERE user_id = ?", (int(event["winner_id"]),))
                    conn.commit()
                except Exception as db_err:
                    logging.error(f"Ошибка сохранения рекорда в БД: {db_err}")
                
    except WebSocketDisconnect:
        if websocket in rooms[room_id]:
            rooms[room_id].remove(websocket)
        if not rooms[room_id]:
            del rooms[room_id]

# --- ОДНОВРЕМЕННЫЙ ЗАПУСК БОТА И СЕРВЕРА В ОДНОМ ПОТОКЕ ---
async def main():
    # Корректно запускаем фоновое прослушивание Telegram API
    bot_task = asyncio.create_task(dp.start_polling(bot))
    
    # Настраиваем конфигурацию веб-сервера Uvicorn
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    
    # Запускаем веб-сервер внутри текущего бесконечного цикла
    await server.serve()
    
    # Останавливаем бота при выключении сервера
    bot_task.cancel()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

import asyncio
import logging
import time
import os
from fastapi import FastAPI, HTTPException, Body
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from config import cursor, conn, SKINS_CONFIG, game_rooms, ADMIN_SECRET
from bot import bot, dp, check_server_win

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    if "last_seen" not in room:
        room["last_seen"] = {}
    room["last_seen"][user_id] = current_time
    
    # Игрок 1 — это создатель комнаты. Все остальные заходят как Игрок 2
    if room["player1"] != user_id:
        if room["player2"] is None:
            room["player2"] = user_id
            room["status"] = "active"
            room["turn_start_time"] = time.time()  # Фиксируем старт хода при входе второго игрока
            
    if room["player1"] and room["player2"] and room["status"] == "wait":
        room["status"] = "active"
        room["turn_start_time"] = time.time()  # Фиксируем время старта первого хода

    # Проверка тайм-аута на ход (10 секунд)
    if room["status"] == "active":
        if "turn_start_time" not in room:
            room["turn_start_time"] = time.time()
            
        elapsed_time = current_time - room["turn_start_time"]
        
        # Если 10 секунд вышло, засчитываем техническое поражение
        if elapsed_time > 10.0:
            room["status"] = "over"
            timed_out_symbol = room["turn"] 
            room["winner"] = "O" if timed_out_symbol == "X" else "X"
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
            
    # ДОБАВЛЕНО ИЗВЛЕЧЕНИЕ draws ИЗ БД ДЛЯ ТЕКУЩЕГО ИГРОКА
    cursor.execute("SELECT IFNULL(wins, 0), IFNULL(losses, 0), IFNULL(draws, 0), current_skin, unlocked_skins FROM users WHERE user_id = ?", (int(user_id) if user_id.isdigit() else 0,))
    user_stats = cursor.fetchone()
    wins, losses, draws, current_skin, unlocked_skins = user_stats if user_stats else (0, 0, 0, 'classic', 'classic')
    
    cursor.execute("SELECT current_skin FROM users WHERE user_id = ?", (int(room["player1"]) if room["player1"].isdigit() else 0,))
    p1_skin_row = cursor.fetchone()
    p1_skin = p1_skin_row[0] if p1_skin_row else "classic"
    
    p2_skin = "classic"
    if room["player2"]:
        cursor.execute("SELECT current_skin FROM users WHERE user_id = ?", (int(room["player2"]) if room["player2"].isdigit() else 0,))
        p2_skin_row = cursor.fetchone()
        p2_skin = p2_skin_row[0] if p2_skin_row else "classic"
        
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
        "win_line": room.get("win_line", []), "user_wins": wins, "user_losses": losses, "user_draws": draws, # ПЕРЕДАЕМ draws НА ФРОНТЕНД
        "current_skin": current_skin, "unlocked_skins": unlocked_skins,
        "rematch_requests": room.get("rematch_requests", []), "rematch_declined": room.get("rematch_declined", False),
        "time_left": room_timeout_left  # ДОБАВЛЕНО СЮДА
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
    room["turn_start_time"] = time.time()  # ИСПРАВЛЕНО: Сбрасываем таймер для следующего хода!
    
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
        # ИСПРАВЛЕНО: ЗАПИСЫВАЕМ НИЧЬЮ ОБОИМ ИГРОКАМ В БД
        p1_id = int(room["player1"]) if room["player1"].isdigit() else 0
        p2_id = int(room["player2"]) if room["player2"].isdigit() else 0
        if p1_id: cursor.execute("UPDATE users SET draws = IFNULL(draws, 0) + 1 WHERE user_id = ?", (p1_id,))
        if p2_id: cursor.execute("UPDATE users SET draws = IFNULL(draws, 0) + 1 WHERE user_id = ?", (p2_id,))
        conn.commit()
    return {"success": True}

@app.post("/api/admin/give")
async def admin_give_wins(data: dict = Body(...)):
    if data.get("secret_password") != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Access Denied")
        
    target_user_id = data.get("target_id")
    amount = data.get("amount", 0)
    
    if not target_user_id: 
        raise HTTPException(status_code=400, detail="Missing user ID")
        
    try:
        target_user_id = int(target_user_id)
        
        # Проверяем, существует ли игрок в базе данных
        cursor.execute("SELECT wins FROM users WHERE user_id = ?", (target_user_id,))
        if not cursor.fetchone(): 
            return {"success": False, "message": "Игрок не найден в БД!"}
            
        # Если админ ввел "top", включаем режим Супер-Игрока
        if str(amount).lower() == "top":
            # Ставим 500 побед (гарантированный ТОП), стираем поражения и ничьи для 100% винрейта
            cursor.execute("UPDATE users SET wins = 500, losses = 0, draws = 0 WHERE user_id = ?", (target_user_id,))
            conn.commit()
            return {"success": True, "message": f"👑 Игрок {target_user_id} теперь в ТОП-1 с 100% Winrate!"}
            
        # Иначе просто начисляем указанное количество побед, как и раньше
        amount = int(amount)
        cursor.execute("UPDATE users SET wins = wins + ? WHERE user_id = ?", (amount, target_user_id))
        conn.commit()
        return {"success": True, "message": f"Начислено {amount} побед игроку {target_user_id}!"}
        
    except Exception as e:
        return {"success": False, "message": f"Ошибка: {str(e)}"}

@app.post("/api/shop/{user_id}")
async def handle_shop(user_id: str, data: dict = Body(...)):
    user_id = int(user_id) if user_id.isdigit() else 0
    action = data.get("action")
    skin_id = data.get("skin_id")
    cursor.execute("SELECT wins, unlocked_skins FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    if not row: raise HTTPException(status_code=404, detail="User not found")
    wins, unlocked_skins = row[0], row[1] or "classic"
    unlocked_list = unlocked_skins.split(",")
    
    if action == "equip":
        if skin_id in unlocked_list:
            cursor.execute("UPDATE users SET current_skin = ? WHERE user_id = ?", (skin_id, user_id))
            conn.commit()
            return {"success": True, "message": "Скин успешно экипирован"}
        return {"success": False, "message": "Скин еще не куплен"}
        
    elif action == "buy":
        if skin_id in unlocked_list: return {"success": False, "message": "Скин уже куплен"}
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
            if user_id not in room["rematch_requests"]:
                room["rematch_requests"].append(user_id)
            room["rematch_declined"] = False
            return {"success": True}
            
    if action == "accept":
        room["board"] = [""] * 9
        room["status"] = "active"
        room["winner"] = ""
        room["win_line"] = []
        room["rematch_requests"] = []
        room["rematch_declined"] = False
        room["player1"], room["player2"] = p2, p1
        room["turn"] = "X"
        # ИСПРАВЛЕНО: Сбрасываем таймер в момент перезапуска игры!
        room["turn_start_time"] = time.time() 
        return {"success": True}

@app.post("/api/reset_room/{room_id}")
async def reset_room(room_id: str, data: dict = Body(...)):
    user_id = str(data.get("user_id"))
    if room_id in game_rooms:
        # Полностью сбрасываем состояние комнаты для нового игрока
        game_rooms[room_id] = {
            "board": [""] * 9,
            "player1": user_id,  # Вы остаетесь создателем
            "player2": None,     # Очищаем старого оппонента
            "status": "wait",    # Возвращаем режим ожидания
            "winner": "",
            "win_line": [],
            "turn": "X",
            "rematch_requests": [],
            "rematch_declined": False,
            "last_seen": {user_id: time.time()},
            "turn_start_time": time.time()
        }
    return {"success": True}

async def main():
    bot_task = asyncio.create_task(dp.start_polling(bot))
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info", workers=1, loop="asyncio")
    server = uvicorn.Server(config)
    await server.serve()
    bot_task.cancel()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

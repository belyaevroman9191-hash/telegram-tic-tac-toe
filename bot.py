from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from config import TOKEN, SERVER_URL, cursor, conn

bot = Bot(token=TOKEN)
dp = Dispatcher()

def check_server_win(b, s):
    win_patterns = [[0, 1, 2], [3, 4, 5], [6, 7, 8], [0, 3, 6], [1, 4, 7], [2, 5, 8], [0, 4, 8], [2, 4, 6]]
    for p in win_patterns:
        if b[p[0]] == s and b[p[1]] == s and b[p[2]] == s:
            return p
    return None

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
                [types.InlineKeyboardButton(text="Играть", web_app=types.WebAppInfo(url=link))]
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
        # Извлекаем пользователей, упорядочивая их по количеству побед
        cursor.execute("SELECT username, IFNULL(wins, 0), IFNULL(losses, 0) FROM users ORDER BY wins DESC, losses ASC LIMIT 10")
        leaders = cursor.fetchall()
        
        if not leaders:
            await message.answer("Таблица лидеров пока пуста! 🏆")
            return
            
        text = "🏆 **ТОП-10 ИГРОКОВ (ПО ПОБЕДАМ):**\n\n"
        
        for i, (username, wins, losses) in enumerate(leaders, 1):
            total_games = wins + losses
            # Считаем винрейт в процентах. Если игр 0, то и винрейт 0.
            winrate = round((wins / total_games) * 100) if total_games > 0 else 0
            
            text += f"{i}. @{username} — {wins} 🥇 / {losses} ❌ | 📈 WR: {winrate}%\n"
            
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        await message.answer("Произошла ошибка при загрузке топа.")

@dp.message(lambda msg: msg.text == "🤝 Позвать друга")
async def cmd_invite_link(message: types.Message):
    bot_info = await bot.get_me()
    deep_link = f"https://t.me/{bot_info.username}?start=game_{message.from_user.id}"
    
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Вызвать друга на дуэль ⚔️", switch_inline_query=f"Вызываю тебя на дуэль! Переходи по ссылке: {deep_link}")]
    ])
    await message.answer("Нажмите на кнопку ниже, выберите друга, и приглашение отправится ему в чат:", reply_markup=markup)

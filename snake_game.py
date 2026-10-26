import random
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

class SnakeGame:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.width = 10
        self.height = 10
        self.snake = [(5, 5), (5, 4), (5, 3)]
        self.direction = 'RIGHT'
        self.food = self._place_food()
        self.score = 0
        self.game_over = False
        self.message_id = None
        self.chat_id = None
        self.running = True
        
    def _place_food(self):
        while True:
            food = (random.randint(0, self.height - 1), random.randint(0, self.width - 1))
            if food not in self.snake:
                return food
    
    def _get_direction_delta(self):
        deltas = {
            'UP': (-1, 0),
            'DOWN': (1, 0),
            'LEFT': (0, -1),
            'RIGHT': (0, 1)
        }
        return deltas[self.direction]
    
    def move(self):
        if self.game_over or not self.running:
            return
        
        dy, dx = self._get_direction_delta()
        head_y, head_x = self.snake[0]
        new_head = (head_y + dy, head_x + dx)
        
        if (new_head[0] < 0 or new_head[0] >= self.height or 
            new_head[1] < 0 or new_head[1] >= self.width):
            self.game_over = True
            return
        
        if new_head in self.snake:
            self.game_over = True
            return
        
        self.snake.insert(0, new_head)
        
        if new_head == self.food:
            self.score += 1
            self.food = self._place_food()
        else:
            self.snake.pop()
    
    def render(self):
        board = []
        for y in range(self.height):
            row = []
            for x in range(self.width):
                if (y, x) == self.snake[0]:
                    row.append('🐍')
                elif (y, x) in self.snake:
                    row.append('🟢')
                elif (y, x) == self.food:
                    row.append('🍎')
                else:
                    row.append('⬛')
            board.append(''.join(row))
        
        return '\n'.join(board)
    
    def get_keyboard(self):
        keyboard = [
            [
                InlineKeyboardButton("️", callback_data="snake_up"),
                InlineKeyboardButton("⬇️", callback_data="snake_down")
            ],
            [
                InlineKeyboardButton("⬅️", callback_data="snake_left"),
                InlineKeyboardButton("➡️", callback_data="snake_right")
            ],
            [
                InlineKeyboardButton("⏹️ Стоп", callback_data="snake_stop")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)
    
    def change_direction(self, new_direction):
        opposites = {
            'UP': 'DOWN',
            'DOWN': 'UP',
            'LEFT': 'RIGHT',
            'RIGHT': 'LEFT'
        }
        if opposites.get(new_direction) != self.direction:
            self.direction = new_direction

active_games = {}

async def start_snake_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if user_id in active_games:
        active_games[user_id].running = False
    
    game = SnakeGame(user_id)
    active_games[user_id] = game
    
    message = await update.message.reply_text(
        f"🐍 *Змейка*\n\n{game.render()}\n\nОчки: {game.score}",
        parse_mode='Markdown',
        reply_markup=game.get_keyboard()
    )
    
    game.message_id = message.message_id
    game.chat_id = chat_id
    
    context.job_queue.run_repeating(
        game_loop,
        interval=2.0,
        first=2.0,
        data={'user_id': user_id, 'chat_id': chat_id}
    )

async def game_loop(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    user_id = data['user_id']
    chat_id = data['chat_id']
    
    if user_id not in active_games:
        return
    
    game = active_games[user_id]
    
    if not game.running or game.game_over:
        if game.game_over:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=game.message_id,
                text=f"💀 *Игра окончена!*\n\n{game.render()}\n\n🏆 Счёт: {game.score}\n\n/snake - начать заново",
                parse_mode='Markdown'
            )
        del active_games[user_id]
        return
    
    game.move()
    
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=game.message_id,
            text=f"🐍 *Змейка*\n\n{game.render()}\n\nОчки: {game.score}",
            parse_mode='Markdown',
            reply_markup=game.get_keyboard()
        )
    except Exception as e:
        print(f"Ошибка обновления: {e}")

async def handle_snake_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    if user_id not in active_games:
        await query.edit_message_text("Игра не найдена. Используйте /snake")
        return
    
    game = active_games[user_id]
    
    direction_map = {
        'snake_up': 'UP',
        'snake_down': 'DOWN',
        'snake_left': 'LEFT',
        'snake_right': 'RIGHT',
    }
    
    direction = direction_map.get(query.data)
    
    if query.data == 'snake_stop':
        game.running = False
        await query.edit_message_text(
            f"⏹️ *Игра остановлена*\n\n{game.render()}\n\n🏆 Счёт: {game.score}\n\n/snake - начать заново",
            parse_mode='Markdown'
        )
        del active_games[user_id]
    elif direction:
        game.change_direction(direction)
        
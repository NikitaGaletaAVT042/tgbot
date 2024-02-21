# Copyright (C) 2017, 2018, 2019, 2020  alfred richardsn
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.


from . import lang
from .bot import bot
from .database import database
from .game import role_titles

import random
from time import time
from pymongo.collection import ReturnDocument
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.apihelper import ApiException
from src import logger


stages = {}


def add_stage(number, time=None, delete=False):
    def decorator(func):
        stages[number] = {'time': time, 'func': func, 'delete': delete}
        return func
    return decorator

def update_game_database(game):
    """Обновляет базу данных с игровыми данными."""
    database.polls.delete_many({'chat': game['chat']})

    new_game = None
    stage_number = game.get('stage', 0)
    stage = stages.get(stage_number + 1)
    
    if stage and stage['delete']:
        database.games.delete_one({'_id': game['_id']})
        new_game = game
    else:
        time_inc = stage['time'](game) if callable(stage['time']) else stage['time']
        new_game = database.games.find_one_and_update(
            {'_id': game['_id']},
            {
                '$set': {
                    'next_stage_time': time() + (time_inc if isinstance(time_inc, (int, float)) else 0),
                    'stage': stage_number + 1,
                    'played': []
                },
                '$inc': {'day_count': int(stage_number == 0)}
            },
            return_document=ReturnDocument.AFTER
        )
    
    return new_game


def execute_stage_actions(game):
    """Выполняет действия для текущего этапа игры."""
    stage_number = game.get('stage', 0)
    stage = stages.get(stage_number)
    if stage:
        try:
            stage['func'](game)
        except ApiException as exception:
            if exception.result.status_code == 403:
                database.games.delete_one({'_id': game['_id']})
                return
    else:
        print(f"Не удалось найти этап {stage_number} для игры {game['_id']}")


def go_to_next_stage(game, inc=1):
    """Переводит игру на следующий этап."""
    if not stages or inc < 0:
        return game  # Нет этапов или некорректное значение инкремента

    new_game = update_game_database(game)
    execute_stage_actions(new_game)
    
    return new_game


def format_roles(game, show_roles=False, condition=lambda p: p.get('alive', True)):
    return '\n'.join(
        [f'{i + 1}. {p["name"]}{" - " + role_titles[p["role"]] if show_roles else ""}'
         for i, p in enumerate(game['players']) if condition(p)]
    )


@add_stage(-4, 90)
def first_stage():
    pass


@add_stage(-3, delete=True)
def cards_not_taken(game):
    bot.edit_message_text(
        'Игра окончена! Игроки не взяли свои карты.',
        chat_id=game['chat'],
        message_id=game['message_id']
    )

@add_stage(-2, 60)
def set_order(game):
    mafia_players = [player for player in game['players'] if player['role'] == 'mafia']
    
    if not mafia_players:
        # Если в игре нет игроков с ролью "мафия"
        bot.send_message(game['chat'], "В игре отсутствует команда мафии. Этап пропускается.")
        go_to_next_stage(game, inc=2)
        return

    # Формирование клавиатуры для выбора порядка
    keyboard = InlineKeyboardMarkup(row_width=8)
    for i, player in enumerate(mafia_players):
        keyboard.add(
            InlineKeyboardButton(
                text=f'{i + 1}',
                callback_data=f'append to order {i + 1}'
            )
        )

    keyboard.row(
        InlineKeyboardButton(
            text='Познакомиться с командой',
            callback_data='mafia team'
        )
    )
    keyboard.row(
        InlineKeyboardButton(
            text='Закончить выбор',
            callback_data='end order'
        )
    )

    # Отправка сообщения с инструкцией и клавиатурой
    message = (
        f'{role_titles["don"].capitalize()}, тебе предстоит сделать свой выбор и определить порядок выстрелов твоей команды.\n'
        'Для этого последовательно нажимай на номера игроков, а после этого нажми на кнопку "Закончить выбор".'
    )
    message_id = bot.send_message(game['chat'], message, reply_markup=keyboard).message_id

    # Обновление информации об игре в базе данных
    database.games.update_one({'_id': game['_id']}, {'$set': {'message_id': message_id}})


@add_stage(-1, 5)
def get_order(game):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(
        InlineKeyboardButton(
            text='✉ Получить приказ',
            callback_data='get order'
        )
    )

    bot.edit_message_text(
        f'{role_titles["don"].capitalize()} записал приказ. {role_titles["mafia"].capitalize()}, получите конверт со своим заданием!',
        chat_id=game['chat'],
        message_id=game['message_id'],
        reply_markup=keyboard
    )

@add_stage(0, lambda g: 90 + max(0, sum(p['alive'] for p in g['players']) - 4) * 35)
def discussion(game):
    # Проверяем, прошла ли уже одна игровая ночь и была ли выбрана жертва
    if game['day_count'] > 1 and 'victim' not in game:
        # Обновляем информацию об игре в базе данных
        database.games.update_one({'_id': game['_id']}, {'$unset': {'victim': True}})
        # Отправляем сообщение об утре
        bot.send_message(
            game['chat'],
            lang.morning_message.format(
                peaceful_night='',
                day=game['day_count'],
                order=format_roles(game)
            ),
        )
    else:
        # Отправляем сообщение об утре с информацией о прошлой ночи
        bot.send_message(
            game['chat'],
            lang.morning_message.format(
                peaceful_night=(
                    'Доброе утро, город!\n'
                    'Этой ночью обошлось без смертей.\n'
                ),
                day=game['day_count'],
                order=format_roles(game)
            ),
        )


def get_votes(game):
    names = [(0, 'Не голосовать')] + [(i + 1, p['name']) for i, p in enumerate(game['players']) if p['alive']]
    return '\n'.join([
        f'{i}. {name}' + (
            (': ' + ', '.join(str(v + 1) for v in game['vote'][str(i - 1)]))
            if str(i - 1) in game['vote'] else ''
        ) for i, name in names
    ])

@add_stage(1, 30)
def vote(game):
    # Проверяем наличие живых игроков
    alive_players = [player for player in game['players'] if player['alive']]
    if not alive_players:
        # Если нет живых игроков, завершаем этап голосования
        bot.send_message(game['chat'], "Нет живых игроков для голосования.")
        go_to_next_stage(game)
        return

    # Формирование клавиатуры для голосования
    keyboard = InlineKeyboardMarkup(row_width=8)
    for i, player in enumerate(alive_players):
        keyboard.add(
            InlineKeyboardButton(
                text=f'{i + 1}',
                callback_data=f'vote {i + 1}'
            )
        )
    keyboard.add(
        InlineKeyboardButton(
            text='Не голосовать',
            callback_data='vote 0'
        )
    )

    # Отправка сообщения с клавиатурой для голосования
    message = lang.vote.format(vote=get_votes(game))
    message_id = bot.send_message(game['chat'], message, reply_markup=keyboard).message_id

    # Обновление информации об игре в базе данных
    database.games.update_one({'_id': game['_id']}, {'$set': {'message_id': message_id}})

@add_stage(2, 20)
def last_words_criminal(game):
    criminal = None
    if game['vote']:
        most_voted = max(game['vote'].values(), key=len)
        candidates = [int(i) for i, votes in game['vote'].items() if len(votes) == len(most_voted)]
        if len(candidates) == 1 and candidates[0] >= 0:
            criminal = candidates[0]

    # Формируем сообщение о преступнике
    if criminal is not None:
        criminal_name = game["players"][criminal]["name"]
        message = f'Народным голосованием в тюрьму был посажен игрок {criminal + 1} ({criminal_name}).'
    else:
        message = 'Город не выбрал преступника.'

    # Отправляем сообщение о преступнике в чат игры
    bot.edit_message_text(
        message,
        chat_id=game['chat'],
        message_id=game['message_id']
    )

    # Обновляем информацию об игре в базе данных
    update_dict = {'$set': {'vote': {}}}
    if criminal is not None:
        update_dict['$set'][f'players.{criminal}.alive'] = False
        update_dict['$set']['victim'] = game['players'][criminal]['id']
    database.games.update_one({'_id': game['_id']}, update_dict)

@add_stage(3, 5)
def night(game):
    try:
        # Отправляем сообщение о начале ночи в чат игры
        message = f'Наступает ночь. Город засыпает. {role_titles["mafia"].capitalize()}, приготовьтесь к выстрелу...'
        message_id = bot.send_message(game['chat'], message).message_id

        # Обновляем информацию об игре в базе данных
        update_dict = {
            '$unset': {'victim': True},
            '$set': {'message_id': message_id}
        }
        database.games.update_one({'_id': game['_id']}, update_dict)
    except ApiException as e:

        logger.error(f'Ошибка API при отправке сообщения: {e}')


@add_stage(4, 5)
def shooting_stage(game):
    try:
        # Получаем список живых игроков и перемешиваем их
        players = [(i, player) for i, player in enumerate(game['players']) if player['alive']]
        random.shuffle(players)

        # Формируем клавиатуру с кнопками выбора игроков для выстрела
        keyboard = InlineKeyboardMarkup(row_width=8)
        keyboard.add(
            *[InlineKeyboardButton(
                text=f'{i + 1}',
                callback_data=f'shot {i + 1}'
            ) for i, player in players]
        )

        # Отправляем сообщение о стадии стрельбы в чат игры
        message = f'{role_titles["mafia"].capitalize()} выбирает жертву.\n{format_roles(game)}'
        bot.edit_message_text(
            message,
            chat_id=game['chat'],
            message_id=game['message_id'],
            reply_markup=keyboard
        )
    except ApiException as e:
        from src import logger
        logger.error(f'Ошибка API при обновлении сообщения: {e}')


@add_stage(5, 10)
def don_stage(game):
    try:
        # Формируем клавиатуру с кнопками выбора игроков для проверки
        keyboard = InlineKeyboardMarkup(row_width=8)
        keyboard.add(
            *[InlineKeyboardButton(
                text=f'{i + 1}',
                callback_data=f'check don {i + 1}'
            ) for i, player in enumerate(game['players']) if player['alive']]
        )

        # Отправляем сообщение о стадии дона в чат игры
        message = f'{role_titles["mafia"].capitalize()} засыпает. {role_titles["don"].capitalize()} совершает свою проверку.\n{format_roles(game)}'
        bot.edit_message_text(
            message,
            chat_id=game['chat'],
            message_id=game['message_id'],
            reply_markup=keyboard
        )
    except ApiException as e:
        logger.error(f'Ошибка API при обновлении сообщения: {e}')


@add_stage(6, 10)
def sheriff_stage(game):
    try:
        # Формируем клавиатуру с кнопками выбора игроков для проверки
        keyboard = InlineKeyboardMarkup(row_width=8)
        keyboard.add(
            *[InlineKeyboardButton(
                text=f'{i + 1}',
                callback_data=f'check sheriff {i + 1}'
            ) for i, player in enumerate(game['players']) if player['alive']]
        )

        # Отправляем сообщение о стадии шерифа в чат игры
        message = f'{role_titles["don"].capitalize()} засыпает. Просыпается {role_titles["sheriff"]} и совершает свою проверку.\n{format_roles(game)}'
        bot.edit_message_text(
            message,
            chat_id=game['chat'],
            message_id=game['message_id'],
            reply_markup=keyboard
        )
    except ApiException as e:
        logger.error(f'Ошибка API при обновлении сообщения: {e}')


@add_stage(7, 20)
def last_words_victim(game):
    try:
        update_dict = {'$set': {'shots': []}}

        mafia_shot = False
        # Проверяем наличие жертвы
        if len(set(game['shots'])) == 1 and len(game['shots']) == sum(p['role'] in ('don', 'mafia') and p['alive'] for p in game['players']):
            victim = game['shots'][0]
            # Если жертва жива, убираем ее из игры
            if game['players'][victim]['alive']:
                mafia_shot = True
                update_dict['$set'][f'players.{victim}.alive'] = False
                update_dict['$set']['victim'] = game['players'][victim]['id']

        # Обновляем информацию об игре в базе данных
        database.games.update_one({'_id': game['_id']}, update_dict)

        # Если жертва была убита мафией, отправляем сообщение о ее смерти
        if not mafia_shot:
            go_to_next_stage(game)
            return

        bot.edit_message_text(
            f'Доброе утро, город!\nПечальные новости: этой ночью был убит игрок {victim+1} ({game["players"][victim]["name"]}).',
            chat_id=game['chat'],
            message_id=game['message_id']
        )
    except ApiException as e:
        logger.error(f'Ошибка API при обновлении сообщения: {e}')


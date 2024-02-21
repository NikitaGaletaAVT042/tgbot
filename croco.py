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
import codecs
import random
import re

import config

# Предварительно загрузите базу слов в память
with codecs.open(config.WORD_BASE, 'r', encoding='cp1251') as base_file:
    WORD_BASE_CONTENT = base_file.read().splitlines()

def get_word():
    return random.choice(WORD_BASE_CONTENT)

def croco_suggestion(suggestion, game, user, message_id):
    if not re.search(r'\b{}\b'.format(game['word']), suggestion):
        return
    
    increments = {'croco.total': 1}
    if user['id'] == game['player']:
        increments['croco.cheat'] = 1
        answer = 'Игра окончена! Нельзя самому называть слово!'
    else:
        increments['croco.win'] = 1
        from pymongo import database
        database.stats.update_one(
            {'id': user['id'], 'chat': game['chat']},
            {'$set': {'name': user['full_name']}, '$inc': {'croco.guesses': 1}},
            upsert=True
        )
        answer = 'Игра окончена! Это верное слово!'

    from src import bot
    bot.send_message(game['chat'], answer, reply_to_message_id=message_id)
    database.games.delete_one({'_id': game['_id']})
    database.stats.update_one(
        {'id': game['player'], 'chat': game['chat']},
        {'$set': {'name': game['full_name']}, '$inc': increments},
        upsert=True
    )

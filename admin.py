"""Admin actions affect only the newly deployed bot."""
import asyncio
import sqlite3
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest

router = Router()
store = None
admins = set()
pending = {}
drafts = {}
running = {}
ADMIN_MENU = """Меню администратора:

🔸 /broadcast - отправить сообщение пользователям бота
🔸 /statistics - показать статистику бота
🔸 /set_start_msg - изменить стартовое сообщение бота
🔸 /set_inline - активировать инлайн-режим
🔸 /referral_system_menu - меню системы реферальных ссылок"""


def allowed(event):
    return event.from_user and event.from_user.id in admins


@router.message(Command('cancel'))
async def cancel(message):
    task = running.pop(message.from_user.id,None)
    if task and task is not asyncio.current_task():
        task.cancel()
    pending.pop(message.from_user.id, None)
    drafts.pop(message.from_user.id, None)
    await message.answer('Отменено.')


@router.message(Command('id'))
async def identity(message):
    await message.answer(f'Ваш Telegram ID: {message.from_user.id}\nID чата: {message.chat.id}')


@router.message(Command('admin', 'statistics', 'broadcast', 'set_start_msg', 'set_inline', 'referral_system_menu'))
async def command(message):
    if not allowed(message):
        await message.answer('Эта команда доступна только администратору.')
        return
    action = message.text.split()[0].split('@')[0][1:]
    if action == 'admin':
        await message.answer(ADMIN_MENU)
    elif action == 'statistics':
        users, groups = store.stats()
        await message.answer(f'👫 Живые участники: {users}\n👥 Групп и каналов: {groups}')
    elif action in {'broadcast', 'set_start_msg'}:
        pending[message.from_user.id] = action
        await message.answer('Отправь сообщение для рассылки.' if action == 'broadcast' else 'Отправь новый текст приветствия (до 4096 символов).\n/cancel — отменить')
    elif action == 'set_inline':
        await message.answer('Инлайн-поиск: ' + ('включён' if store.get('inline', True) else 'выключен'), reply_markup={
            'inline_keyboard': [[{'text': 'Включить', 'callback_data': 'admin:inline:1'}, {'text': 'Выключить', 'callback_data': 'admin:inline:0'}]]})
        await message.answer('Для первого включения активируй /setinline у @BotFather. Bot API не может изменить эту настройку сам.')
    else:
        await referral_menu(message)


async def referral_menu(message):
    labels = [('➕ Создать реферальную ссылку','create'), ('🗑 Удалить реферальную ссылку','delete'),
              ('📈 Статистика по реферальной ссылке','one'), ('📊 Статистика по всем ссылкам','all'), ('Выход','exit')]
    await message.answer('Меню системы реферальных ссылок:', reply_markup={'inline_keyboard': [
        [{'text': name, 'callback_data': f'admin:ref:{action}'}] for name, action in labels]})


@router.callback_query(F.data.startswith('admin:'))
async def callback(call):
    if not allowed(call):
        await call.answer('Нет доступа.', show_alert=True)
        return
    await call.answer()
    action = call.data.split(':')[1:]
    user = call.from_user.id
    if action[0] == 'inline':
        store.set('inline', action[1] == '1')
        await call.message.answer('Настройка сохранена.')
    elif action[0] == 'send':
        draft = drafts.pop(user, None)
        if not draft:
            await call.message.answer('Рассылка отменена или уже отправлена.')
            return
        sent = failed = 0
        for recipient in store.recipients():
            try:
                try:
                    await call.bot.copy_message(recipient, draft.chat.id, draft.message_id)
                except TelegramRetryAfter as error:
                    await asyncio.sleep(error.retry_after)
                    await call.bot.copy_message(recipient, draft.chat.id, draft.message_id)
                sent += 1
            except TelegramForbiddenError:
                store.blocked(recipient)
                failed += 1
            except TelegramBadRequest:
                failed += 1
            await asyncio.sleep(0.05)
        await call.message.answer(f'Отправлено: {sent}. Ошибок: {failed}.')
    elif action[0] == 'ref':
        operation = action[1]
        if operation == 'exit':
            pending.pop(user, None)
            await call.message.answer('Отменено.')
        elif operation in {'create','delete','one'}:
            pending[user] = 'ref_' + operation
            await call.message.answer('Отправь код ссылки: латиница, цифры, _ или -.\n/cancel — отменить')
        else:
            rows = store.refs()
            result = '\n'.join(f'{code}: {count}' for code, name, active, count in rows) or 'Ссылок пока нет.'
            for offset in range(0, len(result), 4000):
                await call.message.answer(result[offset:offset+4000])


@router.message(lambda message: allowed(message) and message.from_user.id in pending)
async def input_value(message):
    user = message.from_user.id
    action = pending[user]
    if action == 'broadcast':
        drafts[user] = message
        pending.pop(user)
        await message.copy_to(message.chat.id)
        await message.answer(f'Получателей: {len(store.recipients())}. Отправить?', reply_markup={
            'inline_keyboard': [[{'text': 'Отправить', 'callback_data': 'admin:send:0'}]]})
        await message.answer('/cancel — отменить')
    elif action == 'set_start_msg':
        if not message.text:
            await message.answer('Нужен текст приветствия.')
            return
        store.set('welcome', message.text)
        pending.pop(user)
        await message.answer('Приветствие сохранено.')
    elif action.startswith('ref_'):
        code = (message.text or '').strip()
        try:
            if action == 'ref_create':
                store.ref(code, code)
                username = (await message.bot.get_me()).username
                await message.answer(f'https://t.me/{username}?start=ref_{code}')
            elif action == 'ref_delete':
                store.disable_ref(code)
                await message.answer('Ссылка отключена. Статистика сохранена.')
            else:
                rows = [row for row in store.refs() if row[0] == code]
                await message.answer(f'Новых пользователей: {rows[0][3]}' if rows else 'Ссылка не найдена.')
            pending.pop(user)
        except (ValueError, sqlite3.IntegrityError):
            await message.answer('Некорректный или уже существующий код. Попробуй другой.')

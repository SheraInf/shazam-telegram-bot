import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import Message, InlineKeyboardMarkup, Chat
import bot
import admin
import os
from store import Store
from core import audio_keyboard, recognition_keyboard


def message():
    result = MagicMock(spec=Message)
    result.chat = SimpleNamespace(id=1)
    result.message_id = 100
    result.from_user = SimpleNamespace(id=1)
    result.video = None
    result.answer = AsyncMock()
    result.answer_audio = AsyncMock()
    result.edit_reply_markup = AsyncMock()
    result.delete = AsyncMock()
    result.edit_caption = AsyncMock()
    result.answer.return_value = message_status()
    return result


def message_status():
    return SimpleNamespace(delete=AsyncMock())


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.db_patch = patch.object(bot,'db',self.store)
        self.db_patch.start()
        bot.username = 'test_music_bot'
        bot.sessions.clear()
        bot.active.clear()
        admin.store = self.store
        admin.admins = {1}
        admin.pending.clear()
        admin.drafts.clear()
        admin.running = {}
        self.track = {'id':'W3_c4-4OMbM','title':'Six Days','artist':'DJ Shadow','duration':233}

    def tearDown(self):
        self.db_patch.stop()
        self.store.db.close()

    async def test_referral_start_accepts_real_chat_type(self):
        m = message()
        m.chat = Chat(id=1,type='private')
        m.text = '/start ref_campaign'
        self.store.ref('campaign','Campaign')
        await bot.start(m)
        self.assertEqual(self.store.stats(),(1,0))
        self.assertEqual(self.store.refs()[0][3],1)

    async def test_middleware_accepts_real_chat_types(self):
        for chat_id,kind in [(1,'private'),(-100,'supergroup')]:
            event = Message.model_validate({'message_id':1,'date':0,'chat':{'id':chat_id,'type':kind},
                'from':{'id':1,'is_bot':False,'first_name':'Test'},'text':'hello'})
            handler = AsyncMock(return_value='handled')
            self.assertEqual(await bot.track_chat(handler,event,{}),'handled')
            handler.assert_awaited_once_with(event,{})
        self.assertEqual(self.store.stats(),(1,1))

    async def test_failed_operation_clears_busy_flag(self):
        m = message()
        operation = AsyncMock(side_effect=RuntimeError('private details'))
        await bot.execute(m,1,operation)
        self.assertNotIn(1,bot.active)
        self.assertNotIn('private details',str(m.answer.call_args_list))

    async def test_busy_request_does_not_start(self):
        bot.active.add(1)
        operation = AsyncMock()
        await bot.execute(message(),1,operation)
        operation.assert_not_awaited()

    async def test_cached_audio_avoids_download(self):
        self.store.save_media('audio:' + self.track['id'],{**self.track,'file_id':'cached-file'})
        m = message()
        with patch.object(bot,'download',new=AsyncMock()) as download:
            await bot.send_audio(m,self.track)
        download.assert_not_awaited()
        self.assertEqual(m.answer_audio.call_args.args[0],'cached-file')
        self.assertIn('song.link',m.answer_audio.call_args.kwargs['caption'])

    async def test_callback_rejects_another_user(self):
        m = message()
        key = bot.remember('query',[self.track],2,1)
        call = SimpleNamespace(data=f'play:{key}:0',message=m,from_user=SimpleNamespace(id=1),answer=AsyncMock())
        await bot.callback(call)
        self.assertTrue(call.answer.call_args.kwargs['show_alert'])
        m.answer_audio.assert_not_awaited()

    async def test_more_sends_new_list_keeps_old(self):
        m = message()
        key = bot.remember('query',[self.track],1,1)
        call = SimpleNamespace(data=f'more:{key}:1',message=m,from_user=SimpleNamespace(id=1),answer=AsyncMock())
        with patch.object(bot,'search',new=AsyncMock(return_value=[self.track])):
            await bot.callback(call)
        m.edit_reply_markup.assert_not_awaited()
        messages = [call for call in m.answer.call_args_list if call.args[0] == '🔍']
        self.assertEqual(len(messages),1)
        self.assertTrue(messages[0].kwargs['reply_markup']['inline_keyboard'])

    async def test_lyrics_missing_fallback_and_remove_button(self):
        m = message()
        self.store.save_media('audio:' + self.track['id'],self.track)
        call = SimpleNamespace(data=f'lyrics:{self.track["id"]}:0',message=m,from_user=SimpleNamespace(id=1),answer=AsyncMock())
        with patch.object(bot,'fetch_lyrics',new=AsyncMock(return_value=None)):
            await bot.callback(call)
        self.assertIn('лирика недоступна',str(m.answer.call_args_list))
        self.assertEqual(len(m.edit_reply_markup.call_args.kwargs['reply_markup']['inline_keyboard']),1)

    async def test_recognition_edits_video_and_sends_audio(self):
        m = message()
        m.video = SimpleNamespace(file_id='video')
        with patch.object(bot,'search',new=AsyncMock(return_value=[self.track])), patch.object(bot,'send_audio',new=AsyncMock()) as send:
            await bot.recognized(m,{'title':'Six Days','subtitle':'DJ Shadow'},1,'abcdef123456')
        m.edit_caption.assert_awaited_once()
        send.assert_awaited_once()

    async def test_unauthorized_admin_cannot_start_broadcast(self):
        m = message()
        m.text = '/broadcast'
        m.from_user.id = 9
        await admin.command(m)
        self.assertNotIn(9,admin.pending)
        self.assertIn('администратору',m.answer.call_args.args[0])

    async def test_broadcast_only_previews_until_confirmed(self):
        m = message()
        m.text = '/broadcast'
        await admin.command(m)
        m.text = 'Test announcement'
        m.copy_to = AsyncMock()
        await admin.input_value(m)
        self.assertIs(admin.drafts[1],m)
        m.copy_to.assert_awaited_once_with(1)

    def test_real_aiogram_validates_keyboards(self):
        InlineKeyboardMarkup.model_validate(audio_keyboard(self.track['id']))
        InlineKeyboardMarkup.model_validate(recognition_keyboard('abcdef123456','A&B','Artist'))

    async def test_cancel_stops_running_task_and_pending_input(self):
        import asyncio
        task = asyncio.create_task(asyncio.sleep(30))
        admin.running[1] = task
        admin.pending[1] = 'set_start_msg'
        await admin.cancel(message())
        await asyncio.gather(task,return_exceptions=True)
        self.assertTrue(task.cancelled())
        self.assertNotIn(1,admin.pending)

    async def test_inline_cached_and_uncached_results(self):
        query = SimpleNamespace(query='six days',offset='',from_user=SimpleNamespace(id=1),answer=AsyncMock())
        with patch.object(bot,'search',new=AsyncMock(return_value=[self.track])):
            await bot.inline(query)
            self.assertEqual(query.answer.call_args.args[0][0].type,'article')
            self.store.save_media('audio:' + self.track['id'],{**self.track,'file_id':'cached-file'})
            await bot.inline(query)
            self.assertEqual(query.answer.call_args.args[0][0].type,'audio')

    async def test_chosen_inline_caches_then_edits_media(self):
        from pathlib import Path
        self.store.save_media('track:' + self.track['id'],self.track)
        api = SimpleNamespace(send_audio=AsyncMock(return_value=SimpleNamespace(audio=SimpleNamespace(file_id='cached-inline'))),
                              edit_message_media=AsyncMock())
        chosen = SimpleNamespace(result_id=self.track['id'],inline_message_id='inline-1',from_user=SimpleNamespace(id=1),bot=api)
        with patch.dict(os.environ,{'INLINE_CACHE_CHAT_ID':'1'}), patch.object(bot,'download',new=AsyncMock(return_value=Path('dummy.mp3'))):
            await bot.inline_chosen(chosen)
        api.send_audio.assert_awaited_once()
        api.edit_message_media.assert_awaited_once()
        self.assertEqual(self.store.media('audio:' + self.track['id'])['file_id'],'cached-inline')

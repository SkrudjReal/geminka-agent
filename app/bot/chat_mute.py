"""Chat-wide mute, including in-flight replies and background deliveries."""

from aiogram.exceptions import TelegramForbiddenError

from app.core.state import state_store


async def mute_columbina(message, bot):
    if message.chat.type not in {'group', 'supergroup'} or not message.from_user:
        return
    anonymous_admin = message.sender_chat and message.sender_chat.id == message.chat.id
    if not anonymous_admin:
        member = await bot.get_chat_member(message.chat.id, message.from_user.id)
        if member.status not in {'administrator', 'creator'}:
            return
    # Repeating the command extends the timer without writing into a muted chat.
    try:
        if not state_store.is_chat_muted(message.chat.id):
            await message.answer('Молчу в этом чате 30 минут.')
    finally:
        state_store.mute_chat(message.chat.id)


async def block_muted_delivery(make_request, bot, method):
    chat_id = getattr(method, 'chat_id', None)
    name = method.__api_method__
    delivery = name.startswith(('send', 'forward', 'copy', 'editMessage')) or name == 'setMessageReaction'
    if isinstance(chat_id, str) and chat_id.lstrip('-').isdigit():
        chat_id = int(chat_id)
    elif isinstance(chat_id, str) and chat_id.startswith('@') and delivery:
        chat_id = (await bot.get_chat(chat_id)).id
    if delivery and isinstance(chat_id, int) and chat_id < 0 and state_store.is_chat_muted(chat_id):
        raise TelegramForbiddenError(method=method, message='Chat muted locally for 30 minutes')
    if delivery and isinstance(chat_id, int) and chat_id < 0:
        from app.services.public_output import protect_method

        method = protect_method(method)
    return await make_request(bot, method)

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.main import setup_bot_commands


@pytest.mark.asyncio
async def test_group_menu_does_not_inherit_private_commands():
    bot = SimpleNamespace(set_my_commands=AsyncMock(), delete_my_commands=AsyncMock())
    await setup_bot_commands(bot)
    scopes = {call.kwargs['scope'].type: call.args[0] for call in bot.set_my_commands.call_args_list}
    assert scopes['default'] == []
    assert [command.command for command in scopes['all_group_chats']] == ['mood']
    assert [command.command for command in scopes['all_chat_administrators']] == ['mood', 'mute_columbina']
    assert {'model', 'memory', 'sandbox'}.issubset({command.command for command in scopes['all_private_chats']})
    assert bot.delete_my_commands.call_args.kwargs['scope'].type == 'all_chat_administrators'

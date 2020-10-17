"""
neo Discord bot
Copyright (C) 2020 nickofolas

neo is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as published
by the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

neo is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with neo.  If not, see <https://www.gnu.org/licenses/>.
"""
import logging
from asyncio import all_tasks
from contextlib import suppress

import aiohttp
import asyncpg
import discord
import neo
from discord.ext import commands
from neo.types import DbCache

from .config_loader import *  # noqa
from .context import Context

__all__ = ('NeoBot',)

logging.basicConfig(level=logging.INFO)

discord.Color.pornhub = discord.Color(0xffa31a)
discord.Color.main = discord.Color(0x84cdff)

intents = discord.Intents.none()
[setattr(intents, name, True) for name in (
    "members",
    "guilds",
    "emojis",
    "presences",
    "messages",
    "reactions")
 ]


async def get_prefix(bot, message):
    if bot.is_closed():
        return
    await bot.wait_until_ready()
    prefix = ['n/']
    if message.guild:
        with suppress(KeyError):  # Not sure why this *would* happen but I guess it could
            prefix = list({*bot.guild_cache[message.guild.id]['prefixes']})
    return commands.when_mentioned_or(*prefix)(bot, message)


class NeoBot(commands.Bot):
    """The bot itself"""

    def __init__(self):
        super().__init__(command_prefix=get_prefix, case_insensitive=True,
                         allowed_mentions=discord.AllowedMentions(
                             everyone=False, users=False, roles=False),
                         intents=intents)
        self.snipes = {}
        self.loop.create_task(self.__ainit__())
        self._cd = commands.CooldownMapping.from_cooldown(
            2.0, 2.5, commands.BucketType.user)
        self.add_check(self.global_cooldown, call_once=True)
        self.add_check(self.check_blacklist)
        self.before_invoke(self.before)

        for ext in neo.conf['exts']:
            self.load_extension(ext)

    async def __ainit__(self):
        self.session = aiohttp.ClientSession()
        self.pool = await asyncpg.create_pool(
            user=neo.secrets.dbuser,
            password=neo.secrets.dbpass,
            database=neo.secrets.db,
            host=neo.secrets.dbhost)
        self.user_cache = await DbCache(db_query="SELECT * FROM user_data",
                                        key='user_id', pool=self.pool)
        self.guild_cache = await DbCache(db_query="SELECT * FROM guild_prefs",
                                         key='guild_id', pool=self.pool)

    def run(self):
        super().run(neo.secrets.bot_token)

    async def get_context(self, message, *, cls=Context):
        return await super().get_context(message, cls=cls)

    def check_blacklist(self, ctx):
        if (p := self.user_cache.get(ctx.author.id)):
            if p['_blacklisted'] is True:
                raise neo.utils.errors.Blacklisted()
            else:
                return True
        else:
            return True

    async def global_cooldown(self, ctx):
        bucket = self._cd.get_bucket(ctx.message)
        retry_after = bucket.update_rate_limit()
        if retry_after:
            raise commands.CommandOnCooldown(bucket, retry_after)
        return True

    async def before(self, ctx):
        if not self.user_cache.get(ctx.author.id):
            with suppress(asyncpg.exceptions.UniqueViolationError):
                await self.pool.execute('INSERT INTO user_data (user_id) VALUES ($1)', ctx.author.id)
                # Adds people to the user_data table whenever they execute their first command
                await self.user_cache.refresh()  # And then updates the user cache

    async def on_ready(self):
        logging.info('Received ready event')
        self.logging_channels = {
            'guild_io': self.get_channel(neo.conf['guild_notifs_channel'])
        }

    async def close(self):  # wrapping all of them into a try except to let it die in peace
        with suppress(Exception):
            [task.cancel() for task in all_tasks(loop=self.loop)]
            await self.session.close()
            await self.conn.close()
        await super().close()

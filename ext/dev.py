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
import asyncio
import copy
import io
import os
import re
import ast
import textwrap
import inspect
import time
import traceback
from contextlib import redirect_stdout
from collections import namedtuple
from typing import Union

import discord
import import_expression
from discord.ext import commands, flags
from tabulate import tabulate

import utils
from config import conf
from utils.formatters import (return_lang_hl, pluralize, group, 
                              insert_return, clean_bytes, format_exception, insert_yield,
                              _wrap_code)
from utils.converters import CBStripConverter, BoolConverter

status_dict = {
    'online': discord.Status.online,
    'offline': discord.Status.offline,
    'dnd': discord.Status.dnd,
    'idle': discord.Status.idle
}
type_dict = {
    'playing': 0,
    'streaming': 'streaming',
    'listening': 2,
    'watching': 3,
    'none': None
}

file_ext_re = re.compile(r"(~?/?(/\w)*)?\.(?P<extension>\w*)")

ShellOut = namedtuple('ShellOut', 'stdout stderr returncode')


async def do_shell(cmd):
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    return ShellOut(stdout, stderr, str(process.returncode))


async def copy_ctx(
        ctx, command_string, *,
        channel: discord.TextChannel = None,
        author: Union[discord.Member, discord.User] = None):
    msg = copy.copy(ctx.message)
    msg.channel = channel or ctx.channel
    msg.author = author or ctx.author
    msg.content = ctx.prefix + command_string
    new_ctx = await ctx.bot.get_context(msg, cls=utils.context.Context)
    return new_ctx


async def _get_results(func, *args, **kwargs):
    if inspect.isasyncgenfunction(func):
        async for result in func(*args, **kwargs):
            yield result
    else:
        yield await func(*args, **kwargs) or ''


# noinspection PyBroadException
class Dev(commands.Cog):
    """Commands made to assist with bot development"""

    def __init__(self, bot):
        self.bot = bot
        self.scope = {}
        self.retain = True
        self._last_result = None

    async def cog_check(self, ctx):
        return await self.bot.is_owner(ctx.author)

    @commands.command(aliases=['sh'])
    async def shell(self, ctx, *, args: CBStripConverter):
        """Invokes the system shell, attempting to run the inputted command"""
        hl_lang = 'sh'
        if match := file_ext_re.search(args):
            hl_lang = return_lang_hl(match.groupdict().get('extension', 'sh'))
        if 'git diff' in args:
            hl_lang = 'diff'
        async with ctx.loading(tick=False):
            shellout = await do_shell(args)
            output = clean_bytes(shellout.stdout) + '\n' + textwrap.indent(clean_bytes(shellout.stderr), '[stderr] ')
            pages = group(output, 1500)
            pages = [str(ctx.codeblock(content=page, lang=hl_lang)) + f"\n`Return code {shellout.returncode}`" for page in pages]
        await ctx.quick_menu(pages, 1, delete_on_button=True, clear_reactions_after=True, timeout=1800)

    @commands.command(name='eval')
    async def eval_(self, ctx, *, body: CBStripConverter):
        """Runs code that you input to the command"""
        env = {
            'bot': self.bot,
            'ctx': ctx,
            'channel': ctx.channel,
            'author': ctx.author,
            'guild': ctx.guild,
            'message': ctx.message,
            '_': self._last_result
        }
        env.update(globals())
        if self.retain:
            env.update(self.scope)
        stdout = io.StringIO()
        to_return = None
        final_results = list()
        async with ctx.loading():
            try:
                import_expression.exec(compile(_wrap_code(body), "<eval>", "exec"), env)
                _aexec = env['func']
                with redirect_stdout(stdout):
                    async for res in _get_results(_aexec, self.scope, self.retain):
                        if res is None:
                            continue
                        self._last_result = res
                        if not isinstance(res, str):
                            res = repr(res)
                        final_results.append(res)
            except Exception as e:
                await format_exception(ctx, e)
                return
            else:
                value = stdout.getvalue() or '' 
                to_return = f'{value}' + '\n'.join(final_results)
        if to_return:
            pages = group(to_return, 1500)
            pages = [str(ctx.codeblock(content=page, lang='py')) for page in pages]
            await ctx.quick_menu(pages, 1, delete_on_button=True, clear_reactions_after=True, timeout=1800)

    @commands.command()
    async def debug(self, ctx, *, command_string):
        """Runs a command, checking for errors and returning exec time"""
        start = time.perf_counter()
        new_ctx = await copy_ctx(ctx, command_string)
        stdout = io.StringIO()
        try:
            with redirect_stdout(stdout):
                await new_ctx.reinvoke()
        except Exception:
            await ctx.message.add_reaction('❗')
            value = stdout.getvalue()
            paginator = commands.Paginator(prefix='```py')
            for line in (value + traceback.format_exc()).split('\n'):
                paginator.add_line(line)
            for page in paginator.pages:
                await ctx.author.send(page)
            return
        end = time.perf_counter()
        await ctx.send(f'Cmd `{command_string}` executed in {end - start:.3f}s')

    @commands.command()
    async def sql(self, ctx, *, query: CBStripConverter):
        """Run SQL statements"""
        is_multistatement = query.count(';') > 1
        if is_multistatement:
            strategy = self.bot.conn.execute
        else:
            strategy = self.bot.conn.fetch

        start = time.perf_counter()
        results = await strategy(query)
        dt = (time.perf_counter() - start) * 1000.0

        rows = len(results)
        if is_multistatement or rows == 0:
            return await ctx.send(f'`{dt:.2f}ms: {results}`')
        rkeys = [*results[0].keys()]
        headers = [textwrap.shorten(col, width=40//len(rkeys), placeholder='') for col in rkeys]
        r = []
        for item in [list(res.values()) for res in results]:
            for i in item:
                r.append(textwrap.shorten(str(i), width=40//len(rkeys), placeholder=''))
        r = group(r, len(rkeys))
        table = tabulate(r, headers=headers, tablefmt='pretty')
        pages = [str(ctx.codeblock(content=page)) for page in group(table, 1500)]
        await ctx.quick_menu(pages, 1, delete_on_button=True, clear_reactions_after=True, timeout=300,
                             template=discord.Embed(
                                 color=discord.Color.main)
                             .set_author(name=f'Returned {rows} {pluralize("row", rows)} in {dt:.2f}ms'))

    @commands.group(name='dev', invoke_without_command=True)
    async def dev_command_group(self, ctx):
        """Some dev commands"""

    @dev_command_group.command(name='delete', aliases=['del'])
    async def delete_bot_msg(self, ctx, message_ids: commands.Greedy[int]):
        for m_id in message_ids:
            converter = commands.MessageConverter()
            m = await converter.convert(ctx, str(m_id))
            if not m.author.bot:
                raise commands.CommandError('I can only delete my own messages')
            await m.delete()
        await ctx.message.add_reaction(ctx.tick(True))

    @dev_command_group.command(name='source', aliases=['src'])
    async def _dev_src(self, ctx, *, obj):
        new_ctx = await copy_ctx(ctx, f'eval return inspect!.getsource({obj})')
        await new_ctx.reinvoke()

    @dev_command_group.command(name='journalctl', aliases=['jctl'])
    async def _dev_journalctl(self, ctx):
        new_ctx = await copy_ctx(ctx, f"sh sudo journalctl -u neo -o cat")
        await new_ctx.reinvoke()

    @flags.add_flag('-s', '--status', default='online', choices=['online', 'offline', 'dnd', 'idle'])
    @flags.add_flag('-p', '--presence', nargs='+', dest='presence')
    @flags.add_flag('-n', '--nick', nargs='?', const='None')
    @flags.command(name='edit')
    async def args_edit(self, ctx, **flags):
        """Edit the bot"""
        if pres := flags.get('presence'):
            if type_dict.get(pres[0]) is None:
                await self.bot.change_presence(status=ctx.me.status)
            elif type_dict.get(pres[0]) == 'streaming':
                pres.pop(0)
                await self.bot.change_presence(activity=discord.Streaming(
                    name=' '.join(pres), url='https://www.twitch.tv/#'))
            else:
                await self.bot.change_presence(
                    status=ctx.me.status,
                    activity=discord.Activity(
                        type=type_dict[pres.pop(0)], name=' '.join(pres)))
        if nick := flags.get('nick'):
            await ctx.me.edit(nick=nick if nick != 'None' else None)
        if stat := flags.get('status'):
            await self.bot.change_presence(status=status_dict[stat.lower()], activity=ctx.me.activity)
        await ctx.message.add_reaction(ctx.tick(True))

    @commands.group(invoke_without_command=True)
    async def sudo(self, ctx, target: Union[discord.Member, discord.User, None], *, command):
        """Run command as another user, or with all checks bypassed"""
        if not isinstance(target, (discord.Member, discord.User)):
            new_ctx = await copy_ctx(ctx, command, author=ctx.author)
            await new_ctx.reinvoke()
            return
        new_ctx = await copy_ctx(ctx, command, author=target)
        await self.bot.invoke(new_ctx)

    @sudo.command(name='in')
    async def _in(
            self, ctx,
            channel: discord.TextChannel,
            *, command):
        new_ctx = await copy_ctx(
            ctx, command, channel=channel)
        await self.bot.invoke(new_ctx)

    @commands.command(aliases=['die', 'kys'])
    async def reboot(self, ctx):
        """Kills all of the bot's processes"""
        response = await ctx.prompt('Are you sure you want to reboot?')
        if response:
            await self.bot.close()

    @commands.command(name='screenshot', aliases=['ss'])
    async def _website_screenshot(self, ctx, *, site):
        """Take a screenshot of a site"""
        async with ctx.loading(tick=False):
            response = await self.bot.session.get('https://magmafuck.herokuapp.com/api/v1', headers={'website': site})
            data = await response.json()
            site = data['website']
            await ctx.send(embed=discord.Embed(colour=discord.Color.main, title=site, url=site).set_image(url=data['snapshot']))

    @flags.add_flag('-m', '--mode', choices=['r', 'l', 'u'], default='r')
    @flags.add_flag('-p', '--pull', action='store_true')
    @flags.add_flag('extension', nargs='*')
    @flags.command(name='extensions', aliases=['ext'])
    async def _dev_extensions(self, ctx, **flags):
        """Manage extensions"""
        async with ctx.loading():
            mode_mapping = {'r': self.bot.reload_extension, 'l': self.bot.load_extension, 'u': self.bot.unload_extension}
            if flags.get('pull'):
                await do_shell('git pull')
            mode = mode_mapping.get(flags['mode'])
            extensions = conf.get('exts') if flags['extension'][0] == '~' else flags['extension']
            for ext in extensions:
                mode(ext)


def setup(bot):
    bot.add_cog(Dev(bot))

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
import textwrap
from contextlib import suppress
from datetime import datetime
from string import ascii_letters
from random import choice
import urllib.parse

import discord
from discord.ext import commands
from humanize import naturaltime as nt
from yarl import URL

import neo
from neo.utils.converters import GitHubConverter
from neo.utils.errors import ApiError
from neo.utils.formatters import prettify_text, from_tz

path_mapping = {'repos': 'repositories'}
gh_emojis = neo.conf['emojis']['github']


class GHUser:
    __slots__ = ('data', 'name', 'url', 'bio', 'av_url', 'location', 'user_id', 'created', 'updated',
                 'refol')

    def __init__(self, data):
        self.data = data
        self.name = data.get('login')
        self.url = data.get('html_url')
        self.bio = data.get('bio')
        self.av_url = URL(data.get('avatar_url')).update_query(f'{choice(ascii_letters)}={choice(ascii_letters)}')
        # ^ This looks unnecessary, but it helps bypass discord's caching the avatar images
        self.location = data.get('location')
        self.user_id = data.get('id')
        self.created = from_tz(data.get('created_at'))
        self.updated = from_tz((data.get('updated_at')))
        self.refol = {k: v for k, v in self.data.items() if k in 
                      ('public_repos', 'public_gists', 'followers', 'following')}


class GHRepo:
    __slots__ = ('data', 'name', 'full_name', 'repo_id', 'owner', 'url', 'description', 'created',
                 'last_push', 'gazers', 'license_id', 'forks', 'language', 'watchers')

    def __init__(self, data):
        self.data = data
        self.name = data.get('name')
        self.full_name = data.get('full_name')
        self.repo_id = data.get('id')
        self.owner = GHUser(data.get('owner'))
        self.url = data.get('html_url')
        self.description = data.get('description')
        self.created = from_tz(data.get('created_at'))
        self.last_push = from_tz(data.get('pushed_at'))
        self.gazers = data.get('stargazers_count')
        self.license_id = self.license()
        self.forks = data.get('forks')
        self.language = data.get('language')
        self.watchers = data.get('subscribers_count')

    def license(self):
        if lic := self.data.get('license'):
            return lic.get('spdx_id')
        return None

async def get_repo_commit_count(session, url):
    async with session.get(url, params={'per_page': 1}) as resp:
        commit_count = len(await resp.json())
    last_page = resp.links.get('last')
    if last_page:
        qs = URL(last_page['url']).query_string
        commit_count = int(dict(urllib.parse.parse_qsl(qs))['page'])
    return f"{commit_count:,}"


class Github(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='user')
    async def git_user(ctx, *, name: GitHubConverter):
        """Fetch data on a github user"""
        async with ctx.loading(tick=False), ctx.bot.session.get(f'https://api.github.com/users/'
                                                                f'{name.get("user")}') as resp:
            if resp.status != 200:
                raise ApiError(f'Received {resp.status}')
            json = await resp.json()
        with suppress(UnboundLocalError):
            user = GHUser(json)
            embed = discord.Embed(title=f'{user.name} ({user.user_id})',
                                  description=textwrap.fill(user.bio, width=40) if user.bio else None, url=user.url,
                                  color=discord.Color.main) \
                .set_thumbnail(url=user.av_url)
            ftext = '\n'.join(f"**{prettify_text(k)}** {v}" for k, v in user.refol.items())
            ftext += f'\n{gh_emojis["location"]} {user.location}' if user.location else ''
            embed.add_field(name='Info', value=ftext)
            embed.set_footer(text=f'Created {nt(datetime.utcnow() - user.created)} | '
                                  f'Updated {nt(datetime.utcnow() - user.updated)}')
            await ctx.send(embed=embed)

    @commands.command(name='repo', usage='<repo owner>/<repo name>')
    async def git_repo(ctx, *, repo: GitHubConverter):
        """Fetch data on a github repository
        MUST be a public repository"""
        async with ctx.loading(tick=False), ctx.bot.session.get(repo_url := f'https://api.github.com/repos/'
                                                                f'{repo.get("user")}/{repo.get("repo")}') as resp:
            if resp.status != 200:
                raise ApiError(f'Received {resp.status}')
            json = await resp.json()
        with suppress(UnboundLocalError):
            repo = GHRepo(json)
            embed = discord.Embed(title=f'{repo.full_name} ({repo.repo_id})',
                                  description=textwrap.fill(repo.description, width=40) if repo.description else None,
                                  color=discord.Color.main, url=repo.url).set_thumbnail(url=repo.owner.av_url)
            push_delta = (datetime.utcnow() - repo.last_push)
            create_delta = (datetime.utcnow() - repo.created)
            fone_txt = str()
            fone_txt += f'**Owner** {repo.owner.name}\n'
            fone_txt += f'**Language** {repo.language}\n'
            fone_txt += f'**Forks** {repo.forks:,}\n'
            fone_txt += f"**Pushed** {nt(push_delta)}"
            ftwo_txt = str()
            ftwo_txt += f'{gh_emojis["license"]} {repo.license_id}\n'
            ftwo_txt += f'{gh_emojis["star"]} {repo.gazers:,}\n'
            ftwo_txt += f'{gh_emojis["watcher"]}  {repo.watchers:,}\n'
            ftwo_txt += f"{gh_emojis['commit']} {await get_repo_commit_count(session=ctx.bot.session, url=(repo_url + '/commits'))}"
            embed.add_field(name='Info', value=fone_txt)
            embed.add_field(name='_ _', value=ftwo_txt) 
            embed.set_footer(text=f'Created {nt(create_delta)}')
            await ctx.send(embed=embed)


def setup(bot):
    for command in Github(bot).get_commands():
        bot.get_command('github').remove_command(command.name)
        bot.get_command('github').add_command(command)
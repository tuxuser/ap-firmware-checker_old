#!/usr/bin/env python3
"""
Discord bot to check for Analogue Pocket firmware updates
"""
import os
import time
import hashlib
import asyncio
from datetime import datetime
from html.parser import HTMLParser
from urllib3.exceptions import NewConnectionError

import requests
import discord

POLLING_TIME_MINS = int(os.environ.get("POLLING_TIME_MINS", "3"))
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID"))

CHECK_INTERVAL_SECS = POLLING_TIME_MINS * 60 # 1 minute
CHECK_URL = 'https://www.analogue.co/support/pocket'

class Event(object):

    def __init__(self):
        self.__eventhandlers = []

    def __iadd__(self, handler):
        self.__eventhandlers.append(handler)
        return self

    def __isub__(self, handler):
        self.__eventhandlers.remove(handler)
        return self

    def __call__(self, *args, **keywargs):
        for eventhandler in self.__eventhandlers:
            eventhandler(*args, **keywargs)

class Parse(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.fw_link = None

    #Defining what the method should output when called by HTMLParser.
    def handle_starttag(self, tag, attrs):
		# Only parse the 'anchor' tag.
        if tag == "a":
            for name,link in attrs:
                if name == "href" and link.startswith("http") and link.lower().endswith('.bin'):
                    self.fw_link = link

class ApUpdateChecker:
    def __init__(self):
        self.html_parser = Parse()
        self.old_hash = None
        self.last_crawl = None

        # Events
        self.on_new_page = Event()
        self.on_new_fw = Event()

    @property
    def fw_link(self):
        return self.html_parser.fw_link

    def check_fw(self):
        resp = requests.get(CHECK_URL)
        if resp.status_code != 200:
            print(f"Error fetching support page: {resp.status_code=}")
            return None

        self.last_crawl = datetime.now()
        page_content_str = resp.content.decode('utf8')

        # Hash the returned page contents
        hash = hashlib.sha256(resp.content).hexdigest()

        # Compare the hash against last-known
        if self.old_hash is None:
            # We didn't have a hash yet, so we'll just store it
            self.old_hash = hash
            # Also gather the current firmware link
            self.html_parser.feed(page_content_str)
            print(f"Initialized with current firmware link: {self.fw_link}")
            return None

        elif hash != self.old_hash:
            # Hash differs! Page was updated!
            self.old_hash = hash

            # Submit new event
            self.on_new_page(page_content_str)

            old_fw_link = self.fw_link
            # Extract fw link from page
            # FW link is stored into global variable: fw_link
            self.html_parser.feed(page_content_str)

            if old_fw_link != self.fw_link:

                # Submit new event
                self.on_new_fw(self.fw_link)

                # Download & save new firmware - in case it goes offline fast
                return self.fw_link


class DiscordAPFWBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # an attribute we can access from our task
        self.channel = None
        self.checker = ApUpdateChecker()
        self.checker.on_new_fw += self.new_firmware_available
        self.checker.on_new_page += self.new_page_available

        self.bg_task = None

    async def send_message(self, text: str):
        # Send message to statically defined channel
        if self.channel:
            if not self.is_closed():
                await self.channel.send(text)
            else:
                print("Bot is closed, cannot send message")
        else:
            print(f"send_message: Failed to find channel {CHANNEL_ID=}")

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')
        self.channel = self.get_channel(CHANNEL_ID)
        if not self.channel:
            print(f"Failed to find channel {CHANNEL_ID=}, Terminating")
            await self.close()
        print('Bot is ready and checking for new fw updates...')
        await self.send_message('AP Firmware bot at your service .. will check every minute for new updates')
        self.bg_task = self.loop.create_task(self.fw_checker_task())

    def new_firmware_available(self, fw_link: str):
        try:
            resp = requests.get(fw_link)
        except Exception as exc:
            print(f'Error downloading firmware: {exc}')
            return

        if resp.status_code == 200:
            filename = resp.headers.get("Content-Disposition",f"filename=firmware_{int(time.time())}.bin").split("filename=")[1]
            fw_filename = f'./download/{filename}'
            print(f'New firmware downloaded -> {fw_filename}')
            with open(fw_filename, 'wb') as f:
                f.write(resp.content)

    def new_page_available(self, new_page: str):
        page_filename = f'./download/page_{int(time.time())}.html'
        print(f'New page downloaded -> {page_filename}')
        with open(page_filename, 'wt') as f:
            f.write(new_page)

    async def fw_checker_task(self):
        await self.wait_until_ready()
        print('Checking for new firmwares...')
        while not self.is_closed():
            try:
                result = self.checker.check_fw()
                if result:
                    await self.send_message(f'New firmware available: {result}')
            except NewConnectionError as exc:
                print(f'Connection error while checking for new firmware: {exc}')
            except Exception as exc:
                print(f'Error while checking for new firmware: {exc}')
                await self.send_message(f'Unhandled error while fetching AP firmware updates: {exc}')

            await asyncio.sleep(CHECK_INTERVAL_SECS)


client = DiscordAPFWBot(intents=discord.Intents.default())
client.run(BOT_TOKEN)

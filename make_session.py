
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

api_id = int(input("API ID: ").strip())
api_hash = input("API HASH: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nYour Telegram session string:")
    print(client.session.save())
    print("\nKeep this secret. Do not share it.")

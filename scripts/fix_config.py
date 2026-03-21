import sys, os, re

path = r'src/config.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

old_block = '''    GMAIL_ADDRESS: str = Field(default="", description="Địa chỉ nhận email")'''

new_block = '''    GMAIL_ADDRESS: str = Field(default="", description="Địa chỉ nhận email")

    # Telegram
    ENABLE_TELEGRAM: bool = Field(default=False, description="Bật thông báo qua Telegram")
    TELEGRAM_BOT_TOKEN: str = Field(default="", description="Token của Telegram Bot")
    TELEGRAM_CHAT_ID: str = Field(default="", description="Chat ID nhận thông báo")'''

if "ENABLE_TELEGRAM" not in text:
    text = text.replace(old_block, new_block)
    with open(path, 'w', encoding='utf-8') as f: f.write(text)
    print("fixed config")

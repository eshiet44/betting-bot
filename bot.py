# bot.py
import discord
from discord.ext import tasks
import pandas as pd
import datetime
import pytz
import gspread
import json
import os
from google.oauth2.service_account import Credentials

# local import
from picks_strategy import generate_picks  # uses your API key inside picks_strategy.py

# ========== CONFIG ==========
DISCORD_TOKEN = ""   # <<< put your Discord bot token here
CHANNEL_ID = 1440507066799100024           # user provided
CSV_FILE = "daily_picks.csv"               # picks_strategy writes this (optional)
SERVICE_ACCOUNT_FILE = r"C:\Users\user\Desktop\Betting-Strategy-Bot\discord-bot-key.json"  # adjust if different
SHEET_NAME = "Daily Picks Tracker"
TIMEZONE = "Africa/Lagos"
CHECK_INTERVAL_MINUTES = 5   # run the strategy every 5 minutes
POSTED_STORE = "posted_picks.json"  # local store of posted fixture ids to avoid duplicates
# ============================

# Discord client
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# timezone
tz = pytz.timezone(TIMEZONE)

# Google Sheets auth
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
gc = gspread.authorize(creds)
sheet = gc.open(SHEET_NAME).sheet1

# ensure posted store exists
if not os.path.exists(POSTED_STORE):
    with open(POSTED_STORE, "w") as f:
        json.dump([], f)

def load_posted_ids():
    with open(POSTED_STORE, "r") as f:
        try:
            return set(json.load(f))
        except:
            return set()

def save_posted_ids(s):
    with open(POSTED_STORE, "w") as f:
        json.dump(list(s), f)

def already_logged_in_sheet(date, match):
    """Return True if a row with same Date and Match already exists in Google Sheet"""
    try:
        all_values = sheet.get_all_records()
        for row in all_values:
            if str(row.get("Date","")).strip() == str(date).strip() and str(row.get("Match","")).strip() == str(match).strip():
                return True
    except Exception as e:
        print("Warning: failed to read sheet for duplicate check:", e)
    return False

def log_to_sheet(date, match, prediction, confidence, result="Pending"):
    try:
        sheet.append_row([date, match, prediction, confidence, result])
    except Exception as e:
        print("Warning: failed to write to sheet:", e)

async def post_pick_to_discord(channel, pick: dict):
    """
    Send formatted message to Discord channel
    """
    title = "🏟 Strategy Pick (Auto)"
    msg = f"""
{title}
📅 Date: {pick['Date']}
⚽ Match: {pick['Match']}
🎯 Prediction: {pick['Prediction']}
🔥 Confidence: {pick['Confidence']}%
📊 HST: {pick.get('HST','N/A')}   AST: {pick.get('AST','N/A')}
💵 B365H: {pick.get('B365H','N/A')}
"""
    await channel.send(msg)

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    strategy_loop.start()

@tasks.loop(minutes=CHECK_INTERVAL_MINUTES)
async def strategy_loop():
    """
    Run strategy, detect new picks, post them immediately and log them.
    """
    print(f"[{datetime.datetime.now(tz).strftime('%Y-%m-%d %H:%M:%S')}] Running strategy...")
    posted = load_posted_ids()

    # generate picks (picks for tomorrow)
    try:
        df = generate_picks()
    except Exception as e:
        print("Error generating picks:", e)
        return

    if df.empty:
        print("No picks generated at this run.")
        return

    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        print("Channel not found. Check CHANNEL_ID!")
        return

    # iterate picks and post new ones
    for _, row in df.iterrows():
        fixture_id = str(row.get("fixture_id", ""))
        date = row.get("Date")
        match = row.get("Match")
        pred = row.get("Prediction")
        conf = row.get("Confidence")

        # skip if we've already posted this fixture_id locally
        if fixture_id and fixture_id in posted:
            print(f"Skipping already-posted fixture {fixture_id} - {match}")
            continue

        # also check Google Sheet to avoid duplicates across runs/hosts
        if already_logged_in_sheet(date, match):
            print("Skipping because sheet already has this pick:", match)
            if fixture_id:
                posted.add(fixture_id)
                save_posted_ids(posted)
            continue

        # Post to discord
        try:
            pick = {
                "Date": date,
                "Match": match,
                "Prediction": pred,
                "Confidence": conf,
                "HST": row.get("HST"),
                "AST": row.get("AST"),
                "B365H": row.get("B365H"),
                "fixture_id": fixture_id
            }
            await post_pick_to_discord(channel, pick)
            # log to google sheet
            log_to_sheet(date, match, pred, conf, result="Pending")
            print("Posted and logged:", match)
            # mark posted
            if fixture_id:
                posted.add(fixture_id)
                save_posted_ids(posted)
        except Exception as e:
            print("Failed to post pick:", e)

# graceful run
client.run(DISCORD_TOKEN)

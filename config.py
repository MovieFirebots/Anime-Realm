import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")
DATABASE_NAME = os.getenv("DATABASE_NAME", "anime_filter_bot")

# Comma-separated list of admin Telegram user IDs
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',') if admin_id.strip()]

# Channel ID where files are automatically indexed (bot must be admin)
# Make sure this is an integer
DB_CHANNEL_ID_STR = os.getenv("DB_CHANNEL_ID")
DB_CHANNEL_ID = int(DB_CHANNEL_ID_STR) if DB_CHANNEL_ID_STR else None


LOG_CHANNEL_ID_STR = os.getenv("LOG_CHANNEL_ID") # Optional, for bot logs/errors
LOG_CHANNEL_ID = int(LOG_CHANNEL_ID_STR) if LOG_CHANNEL_ID_STR else None


# --- Link Shortener Settings ---
# This is a placeholder. Replace with your actual ModijiURL API details.
MODIJI_API_KEY = os.getenv("MODIJI_API_KEY")
MODIJI_API_URL = os.getenv("MODIJI_API_URL", "https://modijiurl.com/api") # Example API endpoint

# URL of your bot's webserver where ModijiURL will redirect after verification
# This will be something like https://your-koyeb-app-name.koyeb.app/verify_callback
# Koyeb automatically provides HTTPS for its services.
APP_BASE_URL = os.getenv("APP_BASE_URL") # e.g., https://my-anime-bot.koyeb.app
VERIFICATION_ENDPOINT = "/verify_callback" # The path for verification callback

TOKEN_EXPIRY_DURATION = 3600  # 1 hour in seconds
TOKENS_PER_VERIFICATION = 10
TOKENS_PER_FILE = 1

# Health check port for Koyeb
PORT = int(os.getenv("PORT", 8080))

# --- UI & Theming (Emojis) ---
NARUTO_EMOJI = "🍥"
ONE_PIECE_EMOJI = "🍖"
AOT_EMOJI = "⚔️"
MHA_EMOJI = "💥"
SUCCESS_EMOJI = "✅"
ERROR_EMOJI = "❌"
INFO_EMOJI = "ℹ️"
SEARCH_EMOJI = "🔍"
FILE_EMOJI = "📁"
TOKEN_EMOJI = "🎟️"
LOADING_EMOJI = "⏳"

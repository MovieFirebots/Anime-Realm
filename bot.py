import logging
import asyncio
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument, InputMediaVideo, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, Filters, CallbackQueryHandler,
    ContextTypes, ConversationHandler
)
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError

import config
import database as db
from utils import parse_filename, generate_verification_token, shorten_link, get_verification_callback_url, format_bytes
from webserver import web_app, set_telegram_bot, run_webserver # Import web_app for running it

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Constants for Callback Data and Conversation Handler States ---
SEARCH_PREFIX = "search_"
FILTER_PREFIX = "filter_"
PAGE_PREFIX = "page_"
DOWNLOAD_PREFIX = "dl_"
CANCEL_PREFIX = "cancel_"

# Filter types
SEASON_FILTER = "s"
EPISODE_FILTER = "e" # Though episode might be too granular for a button filter
QUALITY_FILTER = "q"
LANGUAGE_FILTER = "l"

# For storing current search state in context.user_data
SEARCH_STATE_KEY = "current_search_state"


# --- Helper Functions for Bot ---
def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS

async def log_to_channel(context: ContextTypes.DEFAULT_TYPE, message: str):
    if config.LOG_CHANNEL_ID:
        try:
            await context.bot.send_message(chat_id=config.LOG_CHANNEL_ID, text=message, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Failed to log to channel: {e}")

# --- Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await db.get_or_create_user(user.id, user.username, user.first_name)
    welcome_message = (
        f"Kon'nichiwa, {user.first_name}! {config.NARUTO_EMOJI}\n\n"
        f"I'm your **Auto-Filter Bot**, ready to help you find and manage anime files! {config.ONE_PIECE_EMOJI}\n"
        f"Type /help to see what I can do, or just start searching in a group I'm in! {config.SEARCH_EMOJI}\n\n"
        f"Use /verify in PM to earn {config.TOKEN_EMOJI} tokens!"
    )
    await update.message.reply_text(welcome_message, parse_mode=ParseMode.MARKDOWN)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        f"{config.AOT_EMOJI} **Bot Commands** {config.MHA_EMOJI}\n\n"
        f"{config.SEARCH_EMOJI} **Searching (in groups):**\n"
        f"  - Just type the anime name!\n"
        f"  - Use filters for season, quality, language.\n\n"
        f"{config.TOKEN_EMOJI} **Tokens & Downloads:**\n"
        f"  `/tokens` - Check your token balance (PM).\n"
        f"  `/verify` - Earn tokens by bypassing a link shortener (PM).\n"
        f"  *(1 file download costs {config.TOKENS_PER_FILE} token)*\n\n"
    )
    if is_admin(update.effective_user.id):
        help_text += (
            f"🛡️ **Admin Commands:**\n"
            f"  `/index <channel_id>` - Manually index the last file from a channel.\n"
            f"  *(Forward messages from a channel to this bot to index them if channel_id is not given)*\n"
            f"  `/stats` - View bot statistics.\n"
            f"  `/broadcast <message>` - Broadcast a message to all users (use with caution!).\n"
        )
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

async def index_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(f"{config.ERROR_EMOJI} Only admins can use this command, baka!")
        return

    target_channel_id = None
    if context.args:
        try:
            target_channel_id = int(context.args[0])
        except (ValueError, IndexError):
            await update.message.reply_text(f"{config.INFO_EMOJI} Usage: /index <channel_id> OR forward messages here.")
            return
    elif update.message.forward_from_chat and update.message.forward_from_chat.type == ChatType.CHANNEL:
         target_channel_id = update.message.forward_from_chat.id
    
    if not target_channel_id:
        await update.message.reply_text(f"{config.INFO_EMOJI} Please provide a channel ID or forward messages from the channel to index.")
        return

    # This is a simplified manual index. It would be better to fetch the *last file message*.
    # For now, it assumes the admin forwards the specific file message they want to index.
    # If a channel_id is provided, you'd need to use `context.bot.get_chat_history` or similar,
    # which is more complex to get the *very last* file.
    
    message_to_index = update.message.reply_to_message if update.message.reply_to_message else update.message
    
    if message_to_index.forward_from_chat and message_to_index.forward_from_chat.type == ChatType.CHANNEL:
        # If the /index command itself is a forward, or it's replying to a forward
        actual_message_id = message_to_index.forward_from_message_id
        actual_channel_id = message_to_index.forward_from_chat.id
        
        # Process this forwarded message
        # We need to get the file from this forwarded message.
        file_message = message_to_index # The message object that contains the file info
        
        # Try to get the file from different message attributes
        file_entity = file_message.document or file_message.video or file_message.audio
        if not file_entity:
            await update.message.reply_text(f"{config.ERROR_EMOJI} The replied/forwarded message doesn't contain a recognized file.")
            return

        file_id = file_entity.file_id
        file_name = file_entity.file_name if hasattr(file_entity, 'file_name') else "Unknown_File"
        caption = file_message.caption or ""
        file_type = file_entity.mime_type
        
        metadata = parse_filename(file_name) # You might want to parse caption too

        file_data = {
            "file_id": file_id,
            "file_name": file_name,
            "caption": caption,
            "file_type": file_type,
            "size": file_entity.file_size,
            "channel_id": actual_channel_id, # Original channel
            "message_id": actual_message_id, # Original message ID in that channel
            "series_name": metadata.get("series_name"),
            "season": metadata.get("season"),
            "episode": metadata.get("episode"),
            "quality": metadata.get("quality"),
            "language": metadata.get("language"),
        }

        if await db.add_file(file_data):
            await update.message.reply_text(f"{config.SUCCESS_EMOJI} File '{file_name}' from channel {actual_channel_id} indexed successfully! {config.AOT_EMOJI}")
            await log_to_channel(context, f"<b>Manual Index:</b> Admin {update.effective_user.id} indexed {file_name} from channel {actual_channel_id}")
        else:
            await update.message.reply_text(f"{config.INFO_EMOJI} File '{file_name}' already exists in the database.")
    else:
        await update.message.reply_text(
            f"{config.INFO_EMOJI} To manually index:\n"
            f"1. Use `/index <channel_id>` (this is hard to get specific file).\n"
            f"2. OR, forward the file message(s) from any channel to me, then reply to one of them with `/index`.\n"
            f"3. OR, simply forward the file message(s) from *the target channel* to me. I will auto-detect its origin."
        )


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(f"{config.ERROR_EMOJI} This jutsu is for admins only!")
        return

    total_files = await db.count_total_files()
    total_users = await db.count_total_users()
    
    try:
        db_stats_mongo = await db.get_db_stats()
        # dataSize is typically the most relevant for "used storage by documents"
        used_storage_bytes = db_stats_mongo.get("dataSize", 0) 
        # storageSize is allocated storage, often larger than dataSize due to preallocation/padding
        allocated_storage_bytes = db_stats_mongo.get("storageSize", 0) 
        
        used_storage_hr = format_bytes(used_storage_bytes)
        # "Free" in MongoDB context is complex. `fsUsedSize` and `fsTotalSize` are disk level.
        # We can show allocated vs used within MongoDB.
        # For Koyeb, you'd rely on Koyeb's disk monitoring for overall free disk space.
        # A simple "free within allocation" could be (allocated_storage_bytes - used_storage_bytes)
        internal_free_hr = format_bytes(allocated_storage_bytes - used_storage_bytes)
        
    except Exception as e:
        logger.error(f"Error getting DB stats: {e}")
        used_storage_hr = "N/A"
        internal_free_hr = "N/A"

    stats_message = (
        f"{config.MHA_EMOJI} **Bot Statistics** {config.ONE_PIECE_EMOJI}\n\n"
        f"{config.FILE_EMOJI} Total Stored Files: **{total_files}**\n"
        f"👥 Total Users: **{total_users}**\n"
        f"💾 Used Storage (MongoDB Data): **{used_storage_hr}**\n"
        # f"📁 Free within MongoDB Allocation (approx): **{internal_free_hr}**\n\n" # This might be confusing
        f"🚀 Bot is powered by the Will of Fire! {config.NARUTO_EMOJI}"
    )
    await update.message.reply_text(stats_message, parse_mode=ParseMode.MARKDOWN)

async def tokens_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await db.get_or_create_user(user.id, user.username, user.first_name) # Ensure user exists
    
    # Ensure command is used in PM
    if update.message.chat.type != ChatType.PRIVATE:
        try:
            await update.message.delete() # Delete command from group
            await context.bot.send_message(
                user.id,
                f"{config.INFO_EMOJI} Yo, {user.first_name}! Please use /tokens and /verify in our private chat, okay? Keeps the group tidy! {config.NARUTO_EMOJI}"
            )
        except TelegramError as e:
            logger.warning(f"Could not delete /tokens message or PM user: {e}")
        return

    token_balance = await db.get_user_tokens(user.id)
    message = (
        f"Hey {user.first_name}! {config.ONE_PIECE_EMOJI}\n"
        f"You currently have **{token_balance} {config.TOKEN_EMOJI} tokens**.\n\n"
        f"Need more? Use /verify to earn tokens! {config.AOT_EMOJI}"
    )
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

async def verify_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await db.get_or_create_user(user.id, user.username, user.first_name)

    if update.message.chat.type != ChatType.PRIVATE:
        try:
            await update.message.delete()
            await context.bot.send_message(
                user.id,
                f"{config.INFO_EMOJI} Hey {user.first_name}! Let's handle the /verify process in our PM. It's more secure! {config.MHA_EMOJI}"
            )
        except TelegramError as e:
            logger.warning(f"Could not delete /verify message or PM user: {e}")
        return

    # Check if APP_BASE_URL is configured
    if not config.APP_BASE_URL:
        await update.message.reply_text(
            f"{config.ERROR_EMOJI} Oops! The verification system isn't fully set up by the admin yet (APP_BASE_URL missing). Please try again later."
        )
        logger.error("APP_BASE_URL not configured. /verify command cannot proceed.")
        return

    verification_token = generate_verification_token()
    await db.add_pending_verification(user.id, verification_token)
    
    target_url_for_shortener = get_verification_callback_url(verification_token)
    short_link = await shorten_link(target_url_for_shortener)

    if short_link == target_url_for_shortener and config.MODIJI_API_KEY: # Shortening failed or disabled but API key was present
        message_text = f"{config.ERROR_EMOJI} Couldn't create a short link right now. Please try again in a bit! {config.NARUTO_EMOJI}"
    elif short_link == target_url_for_shortener and not config.MODIJI_API_KEY: # Shortening disabled
         message_text = (
            f"{config.ERROR_EMOJI} The link shortener feature is currently disabled by the admin.\n"
            f"Please contact an admin if you believe this is an error."
         )
         await update.message.reply_text(message_text)
         return
    else:
        message_text = (
            f"{config.TOKEN_EMOJI} **Earn Tokens!** {config.AOT_EMOJI}\n\n"
            f"1. Click the button below to open the link.\n"
            f"2. **Bypass the ads/shortener** on the page you're taken to.\n"
            f"3. Once bypassed successfully, you'll be redirected, and I'll automatically credit your account with **{config.TOKENS_PER_VERIFICATION} tokens**!\n\n"
            f"{config.INFO_EMOJI} This link will expire in **1 hour** or after you complete it.\n\n"
            f"**How to Bypass Guide (Example):**\n"
            f"  - Look for 'Skip Ad', 'Continue', or timer buttons.\n"
            f"  - Close any pop-ups carefully.\n"
            f"  - You might need to click a few times.\n"
            f"  - *Be patient, dattebayo!* {config.NARUTO_EMOJI}"
        )

    keyboard = [[InlineKeyboardButton(f"{config.MHA_EMOJI} Go To Verification Link", url=short_link)]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)


# --- Message Handlers (Auto Indexing & Search) ---

async def auto_index_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles files uploaded/forwarded to the DB_CHANNEL_ID or direct forwards to bot by admin."""
    message = update.effective_message
    
    # Case 1: Message in the configured DB_CHANNEL_ID
    is_db_channel_message = message.chat.id == config.DB_CHANNEL_ID

    # Case 2: Message forwarded to the bot by an admin, originating from any channel
    is_admin_forward = (
        message.chat.type == ChatType.PRIVATE and 
        is_admin(message.from_user.id) and 
        message.forward_from_chat and
        message.forward_from_chat.type == ChatType.CHANNEL
    )

    if not (is_db_channel_message or is_admin_forward):
        return # Not a relevant message for auto-indexing

    file_entity = message.document or message.video or message.audio
    if not file_entity:
        return

    file_id = file_entity.file_id
    file_name = getattr(file_entity, 'file_name', f"Unnamed_{file_id[:8]}")
    caption = message.caption or ""
    file_type = getattr(file_entity, 'mime_type', 'application/octet-stream')
    file_size = getattr(file_entity, 'file_size', 0)

    # Determine original channel and message ID
    original_channel_id = message.forward_from_chat.id if message.forward_from_chat else message.chat.id
    original_message_id = message.forward_from_message_id if message.forward_from_message_id else message.message_id
    
    metadata = parse_filename(file_name)
    if not metadata.get("series_name") and caption: # Try parsing caption if filename didn't yield series
        caption_metadata = parse_filename(caption)
        if caption_metadata.get("series_name"): # Prioritize caption's series name if found
            metadata["series_name"] = caption_metadata["series_name"]
        # Merge other fields if filename didn't provide them
        for key in ["season", "episode", "quality", "language"]:
            if not metadata.get(key) and caption_metadata.get(key):
                metadata[key] = caption_metadata[key]


    file_data = {
        "file_id": file_id,
        "file_name": file_name,
        "caption": caption,
        "file_type": file_type,
        "size": file_size,
        "channel_id": original_channel_id,
        "message_id": original_message_id,
        "series_name": metadata.get("series_name"),
        "season": metadata.get("season"),
        "episode": metadata.get("episode"),
        "quality": metadata.get("quality"),
        "language": metadata.get("language"),
    }

    if await db.add_file(file_data):
        log_msg = (f"<b>Auto-Indexed File:</b> {file_name}\n"
                   f"Series: {metadata.get('series_name')}, S{metadata.get('season')}E{metadata.get('episode')}\n"
                   f"Quality: {metadata.get('quality')}, Lang: {metadata.get('language')}\n"
                   f"From Channel: {original_channel_id}")
        logger.info(f"Auto-indexed: {file_name}")
        if is_admin_forward: # If admin forwarded, confirm to admin
            await message.reply_text(f"{config.SUCCESS_EMOJI} File '{file_name}' auto-indexed from forwarded message!", quote=True)
        await log_to_channel(context, log_msg)
    else:
        logger.info(f"Duplicate file (auto-index attempt): {file_name}")
        # Optionally notify admin if it's a direct forward and was a duplicate
        if is_admin_forward:
            await message.reply_text(f"{config.INFO_EMOJI} File '{file_name}' is already in the database (auto-index).", quote=True)


async def search_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles text messages in groups for searching files."""
    if not update.message or not update.message.text or update.message.chat.type == ChatType.PRIVATE:
        # Only process text messages in groups/supergroups, ignore commands
        if update.message and update.message.text and update.message.text.startswith('/'):
            return
        return

    query = update.message.text.strip()
    if len(query) < 3: # Minimum query length
        # await update.message.reply_text(f"{config.INFO_EMOJI} Search query too short! Enter at least 3 characters.", quote=True)
        return # Don't respond to very short messages to avoid spam

    # Initialize search state
    search_state = {
        "query": query,
        "filters": {"series_name": None, "season": None, "episode": None, "quality": None, "language": None}, # episode filter might be too much for buttons
        "page": 1
    }
    context.user_data[SEARCH_STATE_KEY] = search_state
    
    await display_search_results(update, context, search_state, is_new_search=True)


async def display_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, search_state: dict, is_new_search: bool = False):
    """Displays search results with filter buttons and pagination."""
    query = search_state["query"]
    filters = search_state["filters"]
    page = search_state["page"]

    results, total_files = await db.find_files(query, filters, page, page_size=5) # Display 5 results per page

    if not results and page == 1: # No results at all for this query/filter
        message_text = f"{config.NARUTO_EMOJI} No files found for '`{query}`'"
        if any(filters.values()):
            message_text += " with the current filters."
        message_text += f"\nTry a different search term or adjust filters! {config.ONE_PIECE_EMOJI}"
        if is_new_search and update.message:
            await update.message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN, quote=True)
        elif update.callback_query: # Editing a message from callback
            await update.callback_query.edit_message_text(message_text, parse_mode=ParseMode.MARKDOWN, reply_markup=None)
        return

    if not results and page > 1: # No more results on this page (e.g., user clicked "Next" on last page)
        await update.callback_query.answer("No more results on this page! You're a true explorer! 🗺️", show_alert=True)
        search_state["page"] -= 1 # Revert page
        context.user_data[SEARCH_STATE_KEY] = search_state
        return


    # --- Build Results Message ---
    results_text = 

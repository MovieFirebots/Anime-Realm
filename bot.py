import logging
import asyncio
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument, InputMediaVideo, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, CallbackQueryHandler, # Ensure 'filters' is lowercase here
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
    filters_dict = search_state["filters"] # Renamed to avoid conflict with imported 'filters' module
    page = search_state["page"]

    results, total_files = await db.find_files(query, filters_dict, page, page_size=5) # Display 5 results per page

    if not results and page == 1: # No results at all for this query/filter
        message_text = f"{config.NARUTO_EMOJI} No files found for '`{query}`'"
        if any(filters_dict.values()):
            message_text += " with the current filters_dict."
        message_text += f"\nTry a different search term or adjust filters_dict! {config.ONE_PIECE_EMOJI}"
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
    results_text = f"{config.SEARCH_EMOJI} Search Results for '`{query}`':\n"
    if any(filters_dict.values()):
        active_filters_dict = ", ".join([f"{k.title()}: {v}" for k,v in filters_dict.items() if v])
        results_text += f"Filters: _{active_filters_dict}_\n"
    results_text += "\n"

    # --- Build Inline Keyboard ---
    keyboard = []

    # Filter Buttons (Top Row)
    filter_buttons = []
    # Get available distinct values based on current query AND other active filters_dict
    
    # Quality
    qualities = await db.get_distinct_values("quality", query, {k:v for k,v in filters_dict.items() if k != "quality"})
    if qualities:
        current_q = filters_dict.get("quality","All Q")
        q_text = f"📺 {current_q}" if filters_dict.get("quality") else "📺 Quality"
        filter_buttons.append(InlineKeyboardButton(q_text, callback_data=f"{FILTER_PREFIX}{QUALITY_FILTER}_select"))
    
    # Language
    languages = await db.get_distinct_values("language", query, {k:v for k,v in filters_dict.items() if k != "language"})
    if languages:
        current_l = filters_dict.get("language","All L")
        l_text = f"🏳️ {current_l}" if filters_dict.get("language") else "🏳️ Language"
        filter_buttons.append(InlineKeyboardButton(l_text, callback_data=f"{FILTER_PREFIX}{LANGUAGE_FILTER}_select"))
    
    # Season (if series is somewhat specific)
    # Only show season filter if a series seems to be narrowed down or if results have season info
    seasons = await db.get_distinct_values("season", query, {k:v for k,v in filters_dict.items() if k != "season"})
    if seasons: # Only show if there are seasons to filter by
        current_s = f"S{filters_dict.get('season')}" if filters_dict.get('season') else "All S"
        s_text = f"🌊 {current_s}" if filters_dict.get('season') else "🌊 Season"
        filter_buttons.append(InlineKeyboardButton(s_text, callback_data=f"{FILTER_PREFIX}{SEASON_FILTER}_select"))

    if filter_buttons:
        keyboard.append(filter_buttons)

    # File Result Buttons
    for i, file_doc in enumerate(results):
        file_name = file_doc.get('file_name', 'Unknown File')
        
        button_text = f"{config.FILE_EMOJI} {file_name[:50]}{'...' if len(file_name)>50 else ''}" # Truncate long names
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"{DOWNLOAD_PREFIX}{file_doc['file_id']}")])

    # Pagination Buttons
    total_pages = (total_files + 4) // 5 # 5 items per page
    pagination_buttons = []
    if page > 1:
        pagination_buttons.append(InlineKeyboardButton(f"« Previous {config.AOT_EMOJI}", callback_data=f"{PAGE_PREFIX}prev"))
    if page < total_pages:
        pagination_buttons.append(InlineKeyboardButton(f"Next {config.MHA_EMOJI} »", callback_data=f"{PAGE_PREFIX}next"))
    
    if pagination_buttons:
        keyboard.append(pagination_buttons)
    
    # Cancel button
    keyboard.append([InlineKeyboardButton(f"{config.ERROR_EMOJI} Close Search", callback_data=f"{CANCEL_PREFIX}search")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    full_message = f"{results_text}Page {page}/{total_pages} ({total_files} total matches)"

    if is_new_search and update.message: # New search initiated by a text message
        await update.message.reply_text(full_message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN, quote=True)
    elif update.callback_query: # Editing an existing message due to pagination or filter change
        try:
            await update.callback_query.edit_message_text(full_message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        except TelegramError as e:
            if "Message is not modified" in str(e):
                await update.callback_query.answer("No changes to display.", show_alert=False)
            else:
                logger.error(f"Error editing search results: {e}")
                await update.callback_query.answer(f"{config.ERROR_EMOJI} Error updating results.", show_alert=True)


# --- Callback Query Handler ---

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer() # Acknowledge callback

    user_id = query.from_user.id
    data = query.data

    search_state = context.user_data.get(SEARCH_STATE_KEY)
    if not search_state and not data.startswith(DOWNLOAD_PREFIX) and not data.startswith(CANCEL_PREFIX): # Need state for most operations
        await query.edit_message_text(f"{config.ERROR_EMOJI} Search session expired or invalid. Please start a new search.")
        return

    # --- Download Action ---
    if data.startswith(DOWNLOAD_PREFIX):
        file_id_to_download = data.split(DOWNLOAD_PREFIX)[1]
        user_tokens = await db.get_user_tokens(user_id)

        if user_tokens >= config.TOKENS_PER_FILE:
            await db.update_user_tokens(user_id, -config.TOKENS_PER_FILE)
            file_doc = await db.get_file_by_id(file_id_to_download)
            if file_doc:
                try:
                    caption = (
                        f"{config.NARUTO_EMOJI} Here's your file: **{file_doc.get('file_name', 'N/A')}**\n"
                        f"Series: {file_doc.get('series_name', 'N/A')}\n"
                        f"S{file_doc.get('season', 'N/A')}E{file_doc.get('episode', 'N/A')}\n"
                        f"Quality: {file_doc.get('quality', 'N/A')}, Lang: {file_doc.get('language', 'N/A')}\n\n"
                        f"Thanks for using the bot! {config.ONE_PIECE_EMOJI}"
                    )
                    if 'video' in file_doc['file_type']:
                         await context.bot.send_video(chat_id=user_id, video=file_doc['file_id'], caption=caption, parse_mode=ParseMode.MARKDOWN)
                    elif 'audio' in file_doc['file_type']:
                         await context.bot.send_audio(chat_id=user_id, audio=file_doc['file_id'], caption=caption, parse_mode=ParseMode.MARKDOWN)
                    else: # Default to document
                         await context.bot.send_document(chat_id=user_id, document=file_doc['file_id'], caption=caption, parse_mode=ParseMode.MARKDOWN)
                    
                    await query.message.reply_text(f"{config.SUCCESS_EMOJI} File sent to your PM! Check your chat with me. ({config.TOKENS_PER_FILE} {config.TOKEN_EMOJI} token deducted)")
                    await log_to_channel(context, f"User {user_id} downloaded {file_doc.get('file_name', 'N/A')}. Tokens left: {user_tokens - config.TOKENS_PER_FILE}")

                except TelegramError as e:
                    logger.error(f"Error sending file {file_id_to_download} to {user_id}: {e}")
                    await context.bot.send_message(user_id, f"{config.ERROR_EMOJI} Couldn't send the file due to an error. Your token has not been deducted. Please try again or contact admin. ({e})")
                    await db.update_user_tokens(user_id, config.TOKENS_PER_FILE) # Refund token
            else:
                await context.bot.send_message(user_id, f"{config.ERROR_EMOJI} File not found in database. It might have been removed.")
        else:
            await context.bot.send_message(
                user_id,
                f"{config.ERROR_EMOJI} Not enough tokens! {config.MHA_EMOJI}\n"
                f"You need {config.TOKENS_PER_FILE} {config.TOKEN_EMOJI} to download this file, but you only have {user_tokens}.\n"
                f"Use /verify in PM to earn more tokens!"
            )
            try: 
                await query.message.reply_text(f"@{query.from_user.username} Check your PMs from me! {config.NARUTO_EMOJI}", quote=True)
            except Exception:
                pass 
        return 

    # --- Filter Selection Trigger ---
    if data.startswith(FILTER_PREFIX) and data.endswith("_select"):
        filter_type_code = data.split(FILTER_PREFIX)[1].split("_select")[0]
        
        field_map = {QUALITY_FILTER: "quality", LANGUAGE_FILTER: "language", SEASON_FILTER: "season"}
        db_field = field_map.get(filter_type_code)

        if not db_field: return 

        other_filters_dict = {k:v for k,v in search_state["filters"].items() if k != db_field and v} # Renamed local var
        distinct_values = await db.get_distinct_values(db_field, search_state["query"], other_filters_dict)
        
        if not distinct_values:
            await query.answer(f"No specific {db_field} options found for this search.", show_alert=True)
            return

        filter_choice_buttons = []
        row = []
        for val in distinct_values:
            row.append(InlineKeyboardButton(str(val), callback_data=f"{FILTER_PREFIX}{filter_type_code}_val_{val}"))
            if len(row) == 3:
                filter_choice_buttons.append(row)
                row = []
        if row: 
            filter_choice_buttons.append(row)
        
        filter_choice_buttons.append([InlineKeyboardButton(f"All / Clear this filter", callback_data=f"{FILTER_PREFIX}{filter_type_code}_val_CLEAR")])
        filter_choice_buttons.append([InlineKeyboardButton(f"{config.ERROR_EMOJI} Back to results", callback_data=f"{CANCEL_PREFIX}filter_selection")])
        
        reply_markup = InlineKeyboardMarkup(filter_choice_buttons)
        try:
            await query.edit_message_text(f"Choose {db_field.title()} for '`{search_state['query']}`':", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
        except TelegramError as e:
             if "Message is not modified" not in str(e): logger.error(f"Err editing for filter selection: {e}")
        return

    # --- Filter Value Applied ---
    if data.startswith(FILTER_PREFIX) and "_val_" in data:
        parts = data.split(FILTER_PREFIX)[1].split("_val_")
        filter_type_code = parts[0]
        chosen_value = parts[1]

        field_map = {QUALITY_FILTER: "quality", LANGUAGE_FILTER: "language", SEASON_FILTER: "season"}
        db_field = field_map.get(filter_type_code)

        if not db_field: return

        if chosen_value == "CLEAR":
            search_state["filters"][db_field] = None
        else:
            if db_field == "season":
                try: chosen_value = int(chosen_value)
                except ValueError: pass 
            search_state["filters"][db_field] = chosen_value
        
        search_state["page"] = 1 
        context.user_data[SEARCH_STATE_KEY] = search_state
        await display_search_results(update, context, search_state)
        return

    # --- Pagination ---
    if data.startswith(PAGE_PREFIX):
        action = data.split(PAGE_PREFIX)[1]
        if action == "next":
            search_state["page"] += 1
        elif action == "prev":
            search_state["page"] = max(1, search_state["page"] - 1)
        
        context.user_data[SEARCH_STATE_KEY] = search_state
        await display_search_results(update, context, search_state)
        return

    # --- Cancel Actions ---
    if data.startswith(CANCEL_PREFIX):
        action = data.split(CANCEL_PREFIX)[1]
        if action == "search":
            try:
                await query.edit_message_text(f"{config.AOT_EMOJI} Search closed. Feel free to start a new one!", reply_markup=None)
                if SEARCH_STATE_KEY in context.user_data:
                    del context.user_data[SEARCH_STATE_KEY]
            except TelegramError as e: 
                if "message to edit not found" in str(e).lower():
                    logger.info("Tried to close search but message was already gone.")
                else:
                    raise e
        elif action == "filter_selection": 
            await display_search_results(update, context, search_state)
        return


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.error(f"Update {update} caused error {context.error}", exc_info=context.error)
    await log_to_channel(context, f"<b>ERROR:</b> <code>{context.error}</code>\nUpdate: <code>{update}</code>")
    if isinstance(update, Update) and update.effective_user:
        try:
            await context.bot.send_message(
                chat_id=update.effective_user.id,
                text=f"{config.ERROR_EMOJI} Oh no! Something went wrong on my end. {config.MHA_EMOJI}\nThe developers have been notified. Please try again later!"
            )
        except Exception as e:
            logger.error(f"Failed to send error message to user: {e}")

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "🌟 Start the bot and get a welcome message"),
        BotCommand("help", "ℹ️ Show help message and commands"),
        BotCommand("tokens", "🎟️ Check your token balance (PM)"),
        BotCommand("verify", "🔗 Earn tokens (PM)"),
        BotCommand("stats", "📊 Bot statistics (Admin)"),
        BotCommand("index", "📤 Manually index files (Admin)"),
    ])
    print("Bot commands set!")
    set_telegram_bot(application.bot) 
    print("Telegram Bot instance passed to webserver.")


# --- Main Bot Function ---
def main() -> None:
    """Start the bot."""
    if not config.BOT_TOKEN:
        logger.critical("BOT_TOKEN environment variable not set. Exiting.")
        return
    if not config.MONGO_URI:
        logger.critical("MONGO_URI environment variable not set. Exiting.")
        return
    if not config.ADMIN_IDS:
        logger.warning("ADMIN_IDS not set. Some commands will not be restricted.")
    if not config.DB_CHANNEL_ID:
        logger.warning("DB_CHANNEL_ID not set. Automatic file indexing from a specific channel is disabled.")


    application = Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()

    # --- Command Handlers ---
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    # Ensure config.ADMIN_IDS is a list/tuple of integers for filters.User
    admin_user_filter = filters.User(user_id=config.ADMIN_IDS) if config.ADMIN_IDS else filters.NEVER
    application.add_handler(CommandHandler("index", index_command, filters=filters.ChatType.PRIVATE | admin_user_filter))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("tokens", tokens_command))
    application.add_handler(CommandHandler("verify", verify_command))

    # --- Message Handlers ---
    # Auto-indexing from DB_CHANNEL or admin forwards
    db_channel_filter = filters.Chat(chat_id=config.DB_CHANNEL_ID) if config.DB_CHANNEL_ID else filters.NEVER

    # Corrected definition for admin_forward_filter:
    admin_forward_filter = (
        filters.ChatType.PRIVATE &
        admin_user_filter &
        filters.FORWARDED
        # The check 'message.forward_from_chat.type == ChatType.CHANNEL'
        # is correctly handled inside the auto_index_file function.
    )

    application.add_handler(MessageHandler(
        db_channel_filter | admin_forward_filter,
        auto_index_file
    ))

    # File Search (text messages in groups, not commands)
    # filters.ChatType.GROUPS covers both ChatType.GROUP and ChatType.SUPERGROUP
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS, search_handler))

    # --- Callback Query Handler ---
    application.add_handler(CallbackQueryHandler(button_callback_handler))

    # --- Error Handler ---
    application.add_error_handler(error_handler)

    loop = asyncio.get_event_loop()
    bot_task = loop.create_task(application.run_polling(allowed_updates=Update.ALL_TYPES))
    webserver_task = loop.create_task(run_webserver())

    try:
        logger.info("Bot and Webserver starting...")
        loop.run_until_complete(asyncio.gather(bot_task, webserver_task))
    except KeyboardInterrupt:
        logger.info("Bot shutting down (KeyboardInterrupt)...")
    except Exception as e:
        logger.critical(f"Critical error in main event loop: {e}", exc_info=True)
    finally:
        if application:
             loop.run_until_complete(application.shutdown())
        logger.info("Bot shutdown complete.")


if __name__ == "__main__":
    main()

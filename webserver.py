from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn
import asyncio

from telegram import Bot

import database as db
from config import (
    BOT_TOKEN, TOKENS_PER_VERIFICATION, TOKEN_EMOJI, SUCCESS_EMOJI, ERROR_EMOJI, PORT,
    NARUTO_EMOJI, MHA_EMOJI
)

# Initialize FastAPI app
web_app = FastAPI()
telegram_bot_instance = None # Will be set by bot.py

def set_telegram_bot(bot_instance: Bot):
    global telegram_bot_instance
    telegram_bot_instance = bot_instance

@web_app.get("/healthz", response_class=HTMLResponse)
async def health_check():
    """Health check endpoint for Koyeb."""
    return HTMLResponse(content="<p>OK 🔥</p>", status_code=200)

@web_app.get("/verify_callback", response_class=HTMLResponse)
async def verify_callback(request: Request, token: str = None):
    """
    Endpoint hit after user bypasses the link shortener.
    Verifies the token and grants tokens to the user.
    """
    if not token:
        return HTMLResponse(
            content=f"<h1>{ERROR_EMOJI} Invalid Request</h1><p>No verification token provided.</p>",
            status_code=400
        )

    pending_verification = await db.get_pending_verification(token)

    if not pending_verification:
        return HTMLResponse(
            content=f"<h1>{ERROR_EMOJI} Link Expired or Invalid {MHA_EMOJI}</h1><p>This verification link is no longer valid. Please try generating a new one with /verify.</p>",
            status_code=400
        )

    user_id = pending_verification["user_id"]
    await db.update_user_tokens(user_id, TOKENS_PER_VERIFICATION)

    success_message = (
        f"{SUCCESS_EMOJI} Verification Successful! {NARUTO_EMOJI}\n\n"
        f"You've earned **{TOKENS_PER_VERIFICATION} {TOKEN_EMOJI} tokens!**\n"
        f"Use /tokens to check your new balance."
    )
    
    if telegram_bot_instance:
        try:
            await telegram_bot_instance.send_message(chat_id=user_id, text=success_message, parse_mode='Markdown')
        except Exception as e:
            print(f"Error sending verification success PM to {user_id}: {e}")
            # User might have blocked the bot, that's okay.
    else:
        print("Warning: Telegram bot instance not set in webserver. Cannot send PM.")


    # You can redirect to your bot's Telegram profile or a success page
    # For simplicity, just showing a success message.
    return HTMLResponse(
        content=f"<h1>{SUCCESS_EMOJI} Verification Complete! {NARUTO_EMOJI}</h1><p>You have been awarded {TOKENS_PER_VERIFICATION} {TOKEN_EMOJI} tokens in the Telegram bot. You can close this page.</p>",
        status_code=200
    )

async def run_webserver():
    """Runs the FastAPI web server using Uvicorn."""
    config = uvicorn.Config(web_app, host="0.0.0.0", port=PORT, log_level="info")
    server = uvicorn.Server(config)
    print(f"🚀 Webserver starting on port {PORT}...")
    # Running uvicorn in a separate thread or using asyncio.create_task
    # is better if you want it non-blocking with the bot's polling.
    # For Koyeb, this will likely be the main process.
    await server.serve()

if __name__ == "__main__":
    # This is for standalone testing of the webserver.
    # In production, bot.py will launch this.
    asyncio.run(run_webserver())

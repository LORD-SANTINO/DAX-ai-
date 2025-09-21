import os
import sys
import logging
import asyncio
from typing import List, Optional

# local DB helper (must be in same project)
from db import get_clone

# genai (Gemini)
import google.generativeai as genai

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram import Update

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clone_worker")

# Clone id that this worker will serve
CLONE_USER_ID = os.getenv("CLONE_USER_ID")
if CLONE_USER_ID is None:
    logger.error("CLONE_USER_ID env var required")
    sys.exit(2)
CLONE_USER_ID = int(CLONE_USER_ID)

# Gemini keys and rotation state
GEMINI_API_KEYS: List[str] = [
    k for k in [
        os.getenv("GEMINI_API_KEY_1"),
        os.getenv("GEMINI_API_KEY_2"),
        os.getenv("GEMINI_API_KEY_3"),
        os.getenv("GEMINI_API_KEY_4"),
    ] if k
]
current_key_index = 0
model = None

def configure_gemini():
    """
    Configure genai with the current key index, rotating through keys on failure.
    """
    global model, current_key_index
    if not GEMINI_API_KEYS:
        logger.warning("No GEMINI_API_KEYS configured; model responses will be disabled.")
        model = None
        return
    try:
        genai.configure(api_key=GEMINI_API_KEYS[current_key_index])
        model = genai.GenerativeModel("gemini-1.5-flash")
        logger.info("Gemini configured with key #%d", current_key_index + 1)
    except Exception as e:
        logger.error("Failed to configure Gemini with key #%d: %s", current_key_index + 1, e)
        if len(GEMINI_API_KEYS) > 1:
            current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
            logger.info("Attempting to configure Gemini with next key #%d", current_key_index + 1)
            configure_gemini()
        else:
            model = None
            logger.exception("No working Gemini key found.")

def rotate_gemini_key():
    """
    Rotate to the next key and reconfigure.
    """
    global current_key_index
    if not GEMINI_API_KEYS:
        return
    current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
    configure_gemini()
    logger.warning("Rotated Gemini API key to index %d", current_key_index + 1)

# Telegram handlers ----------------------------------------------------------------

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello — this is your cloned bot. Send a message to chat.")

async def set_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Instructions are managed by the master bot. Please update them there.")

async def clear_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Instructions are managed by the master bot. Please update them there.")

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    For each incoming message:
      - load latest instructions from DB (so updates persist without restart)
      - build prompt using instructions + user message
      - call Gemini model to generate reply (non-blocking via to_thread)
      - on error, rotate keys if the error looks like quota/429 and inform the user
    """
    clone = get_clone(CLONE_USER_ID)
    instructions = clone.get("instructions", "") if clone else ""
    user_text = update.message.text or ""

    # If no model configured, fallback to simple message to avoid total failure
    if model is None:
        # fallback echo but include instructions so users are aware
        response = f"{instructions}\n\nYou said: {user_text}" if instructions else f"You said: {user_text}"
        await update.message.reply_text(response)
        return

    # Build prompt: treat instructions as system-style prefix
    prompt = f"{instructions}\n\nUser: {user_text}" if instructions else user_text

    try:
        # model.generate_content might be blocking; run in a thread
        gen_response = await asyncio.to_thread(model.generate_content, prompt)
        # Response object shape may differ by genai version — .text is expected by previous code
        response_text = getattr(gen_response, "text", None)
        if response_text is None:
            # Fallback to string representation if text not present
            response_text = str(gen_response)
        await update.message.reply_text(response_text)

    except Exception as e:
        err_str = str(e).lower()
        logger.error("Error calling Gemini model for clone %s: %s", CLONE_USER_ID, e)
        # Try simple heuristic for quota/429 -> rotate key and inform user
        if "429" in err_str or "quota" in err_str or "rate" in err_str:
            rotate_gemini_key()
            await update.message.reply_text("⚠️ Model quota or rate limit hit. Trying a different key — please try again.")
        else:
            await update.message.reply_text("⚠️ Sorry, I couldn't process that right now. Try again later.")

# Worker startup -------------------------------------------------------------------

def main():
    # Configure Gemini (if keys present)
    configure_gemini()

    # Ensure clone record exists and get token
    clone = get_clone(CLONE_USER_ID)
    if not clone:
        logger.error("No clone record in DB for user %s", CLONE_USER_ID)
        sys.exit(3)
    token = clone["token"]
    username = clone.get("bot_username", "unknown")
    logger.info("Starting clone worker for user %s (%s)", CLONE_USER_ID, username)

    # Build the Telegram Application and handlers
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("set_instructions", set_instructions))
    app.add_handler(CommandHandler("clear_instructions", clear_instructions))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    # Blocking run
    app.run_polling()

if __name__ == "__main__":
    main()

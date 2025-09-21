import os
import sys
import logging
import asyncio
from typing import List

from db import get_clone, get_referral, REFERRAL_THRESHOLD, save_clone

# genai (Gemini)
import google.generativeai as genai

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram import Update

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clone_worker")

CLONE_USER_ID = os.getenv("CLONE_USER_ID")
if CLONE_USER_ID is None:
    logger.error("CLONE_USER_ID env var required")
    sys.exit(2)
CLONE_USER_ID = int(CLONE_USER_ID)

GEMINI_API_KEYS: List[str] = [
    k for k in [
        os.getenv("GEMINI_API_KEY_1"),
        os.getenv("GEMINI_API_KEY_2"),
    ] if k
]
current_key_index = 0
model = None

def configure_gemini():
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
            configure_gemini()
        else:
            model = None

def rotate_gemini_key():
    global current_key_index
    if not GEMINI_API_KEYS:
        return
    current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
    configure_gemini()
    logger.warning("Rotated Gemini API key to index %d", current_key_index + 1)

def owner_remaining_referrals() -> (int, bool):
    """
    Return (remaining_needed, verified_bool)
    """
    row = get_referral(CLONE_USER_ID)
    if not row:
        # no row => none referred yet
        return REFERRAL_THRESHOLD, False
    remaining = max(0, REFERRAL_THRESHOLD - row["count"])
    return remaining, row["verified"]

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello — this is your cloned bot. Send a message to chat.")

async def set_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.effective_user.id
    if sender_id != CLONE_USER_ID:
        await update.message.reply_text("❌ Only the owner can change instructions.")
        return
    clone = get_clone(CLONE_USER_ID)
    if not clone:
        await update.message.reply_text("❌ Clone record not found.")
        return
    args = context.args or []
    if args:
        new_instructions = " ".join(args).strip()
        try:
            save_clone(CLONE_USER_ID, clone["token"], clone.get("bot_username", ""), new_instructions)
            await update.message.reply_text("✅ Instructions updated.")
        except Exception as e:
            logger.error("Failed saving instructions: %s", e)
            await update.message.reply_text("❌ Failed to save instructions.")
    else:
        current = clone.get("instructions", "") or "(none)"
        await update.message.reply_text(f"📝 Current instructions:\n\n{current}\n\nTo change: /set_instructions [text]\nTo clear: /clear_instructions")

async def clear_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.effective_user.id
    if sender_id != CLONE_USER_ID:
        await update.message.reply_text("❌ Only the owner can clear instructions.")
        return
    clone = get_clone(CLONE_USER_ID)
    if not clone:
        await update.message.reply_text("❌ Clone record not found.")
        return
    try:
        save_clone(CLONE_USER_ID, clone["token"], clone.get("bot_username", ""), "")
        await update.message.reply_text("✅ Instructions cleared.")
    except Exception as e:
        logger.error("Failed clearing instructions: %s", e)
        await update.message.reply_text("❌ Failed to clear instructions.")

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clone = get_clone(CLONE_USER_ID)
    instructions = clone.get("instructions", "") if clone else ""
    user_text = update.message.text or ""

    # If no model configured, fallback to echo with instructions
    if model is None:
        base_response = f"{instructions}\n\nYou said: {user_text}" if instructions else f"You said: {user_text}"
        remaining, verified = owner_remaining_referrals()
        if not verified:
            watermark = (
                "\n\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                "🔹 Cloned by @daxotp_bot\n"
                f"📊 {remaining} referrals needed to remove watermark"
            )
            base_response += watermark
        await update.message.reply_text(base_response)
        return

    prompt = f"{instructions}\n\nUser: {user_text}" if instructions else user_text

    try:
        gen_response = await asyncio.to_thread(model.generate_content, prompt)
        response_text = getattr(gen_response, "text", None) or str(gen_response)
        # Append watermark if owner still not verified
        remaining, verified = owner_remaining_referrals()
        if not verified:
            watermark = (
                "\n\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
                "🔹 Cloned by @daxotp_bot\n"
                f"📊 {remaining} referrals needed to remove watermark"
            )
            response_text += watermark
        await update.message.reply_text(response_text)
    except Exception as e:
        err_str = str(e).lower()
        logger.error("Gemini error: %s", e)
        if "429" in err_str or "quota" in err_str or "rate" in err_str:
            rotate_gemini_key()
            await update.message.reply_text("⚠️ Model quota/rate limit hit. Trying another key — please try again.")
        else:
            await update.message.reply_text("⚠️ Sorry, I couldn't process that right now. Try again later.")

def main():
    configure_gemini()
    clone = get_clone(CLONE_USER_ID)
    if not clone:
        logger.error("No clone record in DB for user %s", CLONE_USER_ID)
        sys.exit(3)
    token = clone["token"]
    username = clone.get("bot_username", "unknown")
    logger.info("Starting clone worker for user %s (%s)", CLONE_USER_ID, username)

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("set_instructions", set_instructions))
    app.add_handler(CommandHandler("clear_instructions", clear_instructions))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    app.run_polling()

if __name__ == "__main__":
    main()

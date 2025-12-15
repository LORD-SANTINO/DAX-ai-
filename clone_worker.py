import os
import sys
import logging
import asyncio
from typing import List

from db import get_clone, save_clone, get_referral, REFERRAL_THRESHOLD

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
        os.getenv("GEMINI_API_KEY_4"),
        os.getenv("GEMINI_API_KEY_5"),
        os.getenv("GEMINI_API_KEY_6"),
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
    row = get_referral(CLONE_USER_ID)
    if not row:
        return REFERRAL_THRESHOLD, False
    remaining = max(0, REFERRAL_THRESHOLD - row["count"])
    return remaining, row["verified"]

# New start handler that identifies owner
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender = update.effective_user
    sender_name = sender.first_name or sender.username or str(sender.id)
    clone = get_clone(CLONE_USER_ID)
    owner_username = clone.get("owner_username", "") if clone else ""
    # If owner_username is empty, fall back to textual owner id
    owner_display = f"@{owner_username}" if owner_username else f"user_{CLONE_USER_ID}"
    # Compose greeting exactly as requested
    await update.message.reply_text(f"Hey {sender_name}, I am {owner_display} ai do you understand what I mean?")

# Keep set_instructions / clear_instructions / chat_handler as you previously implemented.
# Below are minimal placeholders (replace with your full implementations that call model.generate_content etc.)

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
            save_clone(CLONE_USER_ID, clone["token"], clone.get("bot_username", ""), new_instructions, clone.get("owner_username",""))
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
        save_clone(CLONE_USER_ID, clone["token"], clone.get("bot_username", ""), "", clone.get("owner_username",""))
        await update.message.reply_text("✅ Instructions cleared.")
    except Exception as e:
        logger.error("Failed clearing instructions: %s", e)
        await update.message.reply_text("❌ Failed to clear instructions.")

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # keep your previously working model code here; this placeholder maintains watermark logic
    clone = get_clone(CLONE_USER_ID)
    instructions = clone.get("instructions", "") if clone else ""
    user_text = update.message.text or ""
    if model is None:
        base_response = f"{instructions}\n\nYou said: {user_text}" if instructions else f"You said: {user_text}"
        remaining, verified = owner_remaining_referrals()
        if not verified:
            watermark = (
                "\n\n┈┈┈┈┈┈┈┈┈┈┈┈\n"
                "🔹 Made by @aimastercreatorrobot\n"
                f"📊 {remaining} referrals needed to remove watermark"
            )
            base_response += watermark
        await update.message.reply_text(base_response)
        return

    prompt = f"{instructions}\n\nUser: {user_text}" if instructions else user_text
    try:
        gen_response = await asyncio.to_thread(model.generate_content, prompt)
        response_text = getattr(gen_response, "text", None) or str(gen_response)
        remaining, verified = owner_remaining_referrals()
        if not verified:
            watermark = (
                "\n\n┈┈┈┈┈┈┈┈┈┈┈┈\n"
                "🔹 Made by @aimastercreatorrobot\n"
                f"📊 {remaining} referrals needed to remove watermark"
            )
            response_text += watermark
        await update.message.reply_text(response_text)
    except Exception as e:
        err_str = str(e).lower()
        logger.error("Gemini error: %s", e)
        if "429" in err_str or "quota" in err_str or "rate" in err_str:
            rotate_gemini_key()
            await update.message.reply_text("Bug error😥 — please try again.")
        else:
            await update.message.reply_text("⚠️ Sorry, I couldn't process that right now. Try again later")

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

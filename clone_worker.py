import os
import sys
import logging
from db import get_clone
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from telegram import Update

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("clone_worker")

CLONE_USER_ID = os.getenv("CLONE_USER_ID")
if CLONE_USER_ID is None:
    logger.error("CLONE_USER_ID env var required")
    sys.exit(2)
CLONE_USER_ID = int(CLONE_USER_ID)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Hello from your cloned bot! Send a message to chat.")

async def set_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("This worker does not support changing instructions via UI; update via master.")

async def clear_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("This worker does not support clearing instructions via UI; update via master.")

async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Load instructions from the DB each time so master-updates persist without restarting worker
    clone = get_clone(CLONE_USER_ID)
    instructions = clone.get("instructions", "") if clone else ""
    user_text = update.message.text or ""
    response = f"{instructions}\n\nYou said: {user_text}" if instructions else f"You said: {user_text}"
    await update.message.reply_text(response)

def main():
    clone = get_clone(CLONE_USER_ID)
    if not clone:
        logger.error("No clone record in DB for user %s", CLONE_USER_ID)
        sys.exit(3)
    token = clone["token"]
    username = clone["bot_username"] or "unknown"

    logger.info("Starting clone worker for user %s (%s)", CLONE_USER_ID, username)
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("set_instructions", set_instructions))
    app.add_handler(CommandHandler("clear_instructions", clear_instructions))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat_handler))

    # Blocking run
    app.run_polling()

if __name__ == "__main__":
    main()

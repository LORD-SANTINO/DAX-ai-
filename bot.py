import os
import logging
import google.generativeai as genai
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from telegram.exceptions import Forbidden

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========= Gemini API Keys =========
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
]
current_key_index = 0

def configure_gemini():
    global model
    genai.configure(api_key=GEMINI_API_KEYS[current_key_index])
    model = genai.GenerativeModel("gemini-1.5-flash")

configure_gemini()

# Main bot token from env
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# ====== For /clone command conversation ======
ASK_TOKEN = range(1)

# Store active cloned app instances {user_id: Application}
cloned_apps = {}

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Hello! I’m your AI bot (Gemini-powered). Send me a message!")

# Switch to next API key for Gemini
def switch_key():
    global current_key_index
    current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
    configure_gemini()
    logger.warning(f"Switched to Gemini API key #{current_key_index + 1}")

# Chat handler for main bot
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    try:
        response = model.generate_content(user_message)
        await update.message.reply_text(response.text)

    except Exception as e:
        if "429" in str(e):
            switch_key()
            await update.message.reply_text("⚠️ Quota exceeded, switching API key... Please try again.")
        else:
            logger.error(f"Error: {e}")
            await update.message.reply_text(f"⚠️ Error: {e}")

# ---- /clone command handler to start token collection ----
async def clone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Please send me your Telegram bot token to clone this bot's behavior."
    )
    return ASK_TOKEN

# ---- Receive token from user and validate ----
async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_token = update.message.text.strip()

    # Validate token by calling getMe through a temporary app
    try:
        test_app = ApplicationBuilder().token(user_token).build()
        me = await test_app.bot.get_me()
        # If no exception, token is valid
        await update.message.reply_text(
            f"✅ Token valid! Your bot @{me.username} will now clone this bot."
        )
        # Save token in user data
        context.user_data["cloned_token"] = user_token

        # Start cloned bot instance for this user
        await start_cloned_bot(update.effective_user.id, user_token)
        
        return ConversationHandler.END

    except Forbidden:
        await update.message.reply_text(
            "❌ Invalid token. Please send a valid Telegram bot token or /cancel."
        )
        return ASK_TOKEN
    except Exception as e:
        await update.message.reply_text(f"❌ Error validating token: {e}")
        return ConversationHandler.END

# ---- Cancel handler ----
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Clone operation cancelled.")
    return ConversationHandler.END

# ---- Start a cloned bot instance for given user token ----
async def start_cloned_bot(user_id: int, token: str):
    if user_id in cloned_apps:
        # Stop existing instance before restarting
        cloned_apps[user_id].stop()

    # Create new app for user cloned bot
    app = ApplicationBuilder().token(token).build()

    # Add handlers - reuse your bot's handlers here
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    # Run the app in non-blocking way
    import asyncio
    asyncio.create_task(app.run_polling())

    # Save app instance to dictionary
    cloned_apps[user_id] = app

    logger.info(f"Started cloned bot for user {user_id}")

# Main function for the master bot (the one handling /clone)
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clone", clone))

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("clone", clone)],
        states={
            ASK_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    logger.info("Master bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

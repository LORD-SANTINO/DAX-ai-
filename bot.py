import os
import logging
import asyncio
import google.generativeai as genai
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from telegram.error import Forbidden

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ========= Gemini API Keys =========
GEMINI_API_KEYS = [
    key for key in [
        os.getenv("GEMINI_API_KEY_1"),
        os.getenv("GEMINI_API_KEY_2"),
        os.getenv("GEMINI_API_KEY_3"),
    ] if key is not None  # Filter out None values
]

if not GEMINI_API_KEYS:
    raise ValueError("No Gemini API keys found in environment variables")

current_key_index = 0
model = None

def configure_gemini():
    global model, current_key_index
    try:
        genai.configure(api_key=GEMINI_API_KEYS[current_key_index])
        model = genai.GenerativeModel("gemini-1.5-flash")
        logger.info(f"Successfully configured Gemini with key #{current_key_index + 1}")
    except Exception as e:
        logger.error(f"Failed to configure Gemini: {e}")
        # Try the next key if available
        if len(GEMINI_API_KEYS) > 1:
            current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
            configure_gemini()
        else:
            raise

configure_gemini()

# Main bot token from env
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is required")

# ====== For /clone command conversation ======
ASK_TOKEN = range(1)

# Store active cloned app instances {user_id: Application}
cloned_apps = {}

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Hello! I'm your AI bot (Gemini-powered). Send me a message!")

# Switch to next API key for Gemini
def switch_key():
    global current_key_index
    current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
    configure_gemini()
    logger.warning(f"Switched to Gemini API key #{current_key_index + 1}")

# Chat handler for main bot
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if model is None:
        await update.message.reply_text("⚠️ Bot is not properly configured. Please contact the administrator.")
        return
        
    user_message = update.message.text
    try:
        response = model.generate_content(user_message)
        await update.message.reply_text(response.text)

    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            switch_key()
            await update.message.reply_text("⚠️ Quota exceeded, switching API key... Please try again.")
        else:
            logger.error(f"Error: {e}")
            await update.message.reply_text("⚠️ Sorry, I encountered an error processing your request.")

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
        async with ApplicationBuilder().token(user_token).build() as test_app:
            me = await test_app.bot.get_me()
        # If no exception, token is valid
        await update.message.reply_text(
            f"✅ Token valid! Your bot @{me.username} will now clone this bot."
        )
        
        # Start cloned bot instance for this user
        await start_cloned_bot(update.effective_user.id, user_token)
        
        return ConversationHandler.END

    except Forbidden:
        await update.message.reply_text(
            "❌ Invalid token. Please send a valid Telegram bot token or /cancel."
        )
        return ASK_TOKEN
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        await update.message.reply_text("❌ Error validating token. Please try again or /cancel.")
        return ASK_TOKEN

# ---- Cancel handler ----
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Clone operation cancelled.")
    return ConversationHandler.END

# ---- Start a cloned bot instance for given user token ----
async def start_cloned_bot(user_id: int, token: str):
    # Stop existing instance if it exists
    if user_id in cloned_apps:
        try:
            await cloned_apps[user_id].updater.stop()
            await cloned_apps[user_id].stop()
            await cloned_apps[user_id].shutdown()
        except Exception as e:
            logger.error(f"Error stopping existing bot: {e}")
        del cloned_apps[user_id]

    # Create new app for user cloned bot
    app = ApplicationBuilder().token(token).build()

    # Add handlers - reuse your bot's handlers here
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    # Run the app in background
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Save app instance to dictionary
    cloned_apps[user_id] = app
    logger.info(f"Started cloned bot for user {user_id}")

# Graceful shutdown handler
async def shutdown_application():
    """Shutdown all cloned bot instances"""
    logger.info("Shutting down all cloned bots...")
    for user_id, app in list(cloned_apps.items()):
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            del cloned_apps[user_id]
            logger.info(f"Stopped cloned bot for user {user_id}")
        except Exception as e:
            logger.error(f"Error stopping cloned bot for user {user_id}: {e}")

# Main function for the master bot (the one handling /clone)
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("clone", clone)],
        states={
            ASK_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    # Register shutdown handler using the correct method
    # The python-telegram-bot v20.x uses a different approach for shutdown handlers
    # We'll handle shutdown through signal handlers instead
    
    logger.info("Master bot is running...")
    
    # Run the application with proper signal handling
    try:
        app.run_polling()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    finally:
        # Manual cleanup on shutdown
        asyncio.run(shutdown_application())

if __name__ == "__main__":
    main()

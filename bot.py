import os
import logging
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========= Gemini API Keys =========
# Store your keys in a list
GEMINI_API_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
]
current_key_index = 0

def configure_gemini():
    """Configure Gemini with the current API key."""
    global model
    genai.configure(api_key=GEMINI_API_KEYS[current_key_index])
    model = genai.GenerativeModel("gemini-1.5-flash")

# Load first key at startup
configure_gemini()

# Telegram token
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Hello! I’m your AI bot (Gemini-powered). Send me a message!")

# Switch to next API key
def switch_key():
    global current_key_index
    current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
    configure_gemini()
    logger.warning(f"Switched to Gemini API key #{current_key_index + 1}")

# Chat handler
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    try:
        response = model.generate_content(user_message)
        await update.message.reply_text(response.text)

    except Exception as e:
        if "429" in str(e):  # Quota exceeded
            switch_key()
            await update.message.reply_text("⚠️ Quota exceeded, switching API key... Please try again.")
        else:
            logger.error(f"Error: {e}")
            await update.message.reply_text(f"⚠️ Error: {e}")

# Main function
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    logger.info("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()

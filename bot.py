import os
import logging
import asyncio
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler, CallbackQueryHandler
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
    ] if key is not None
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
        if len(GEMINI_API_KEYS) > 1:
            current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
            configure_gemini()
        else:
            raise

configure_gemini()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is required")

# ====== Conversation States ======
ASK_TOKEN, ASK_INSTRUCTIONS = range(2)

# Store data
cloned_apps = {}
user_instructions = {}
user_share_counts = {}  # {user_id: share_count}
user_share_verified = {}  # {user_id: True/False}

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Check if user needs to share
    if user_id in user_share_counts and user_share_counts[user_id] < 5:
        remaining = 5 - user_share_counts[user_id]
        await update.message.reply_text(
            f"📣 Share with {remaining} more people to remove the watermark!\n\n"
            "Use /share to get your referral link."
        )
        return
    
    await update.message.reply_text(
        "🤖 Hello! I'm your AI bot (Gpt-powered). Send me a message now!\n\n"
        "Use /clone to create your own AI bot!🙂"
    )

# Share command with referral system
async def share_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    referral_link = f"https://t.me/daxotp_bot?start=ref_{user_id}"
    
    keyboard = [
        [InlineKeyboardButton("📤 Share with Friends", url=f"https://t.me/share/url?url={referral_link}&text=Check%20out%20this%20awesome%20AI%20bot%20that%20can%20clone%20itself%20with%20custom%20instructions!%20🚀")],
        [InlineKeyboardButton("✅ I've Shared", callback_data="shared")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"📣 Share this bot with 5 friends to remove the watermark!\n\n"
        f"🔗 Your referral link: {referral_link}\n\n"
        "After sharing, click 'I've Shared' to verify!",
        reply_markup=reply_markup
    )

# Handle share callback
async def share_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    if query.data == "shared":
        # Initialize share count if not exists
        if user_id not in user_share_counts:
            user_share_counts[user_id] = 0
        
        if user_share_counts[user_id] < 5:
            user_share_counts[user_id] += 1
            remaining = 5 - user_share_counts[user_id]
            
            if remaining > 0:
                await query.message.edit_text(
                    f"✅ Thanks for sharing! {remaining} more to go!\n\n"
                    "Keep sharing to remove the watermark completely!. You can do this💪"
                )
            else:
                user_share_verified[user_id] = True
                await query.message.edit_text(
                  "🌊 **Watermark Cleared**\n\n"
                  "Your generosity has been rewarded.\n"
                  "From now on, your conversations will flow\n"
                  "without any distractions.\n\n"
                  "Thank you for spreading the Bot.🙏"
                )
        else:
            await query.answer("You've already completed sharing! 🎉")
    
    await query.answer()

# Check referral links
async def check_referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].startswith('ref_'):
        try:
            referrer_id = int(context.args[0][4:])
            if referrer_id in user_share_counts and user_share_counts[referrer_id] < 5:
                user_share_counts[referrer_id] += 1
                logger.info(f"User {referrer_id} got a referral!yayyy🤧 Total: {user_share_counts[referrer_id]}")
        except ValueError:
            pass

# Enhanced chat handler with watermark
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if model is None:
        await update.message.reply_text("⚠️ Bot is not properly configured. Please contact the administrator.@dn_feedbackbot")
        return
        
    user_message = update.message.text
    user_id = update.effective_user.id
    
    try:
        # Check if user has custom instructions
        instructions = user_instructions.get(user_id, "")
        enhanced_prompt = f"{instructions}\n\nUser: {user_message}" if instructions else user_message
        
        response = model.generate_content(enhanced_prompt)
        response_text = response.text
        
        # Add watermark if user hasn't shared enough
        if user_id not in user_share_verified or not user_share_verified[user_id]:
            watermark = "\n\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n🔹 Cloned by @daxotp_bot\nShare with 5 friends to remove this"
            response_text += watermark
        
        await update.message.reply_text(response_text)

    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            switch_key()
            await update.message.reply_text("⚠️ Quota exceeded, switching API key... Please try again.")
        else:
            logger.error(f"Error: {e}")
            await update.message.reply_text("⚠️ Sorry, I encountered an error processing your request.")

# Switch to next API key for Gemini
def switch_key():
    global current_key_index
    current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
    configure_gemini()
    logger.warning(f"Switched to API key #{current_key_index + 1}")

# Custom instructions command
async def set_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.args:
        instructions = " ".join(context.args)
        user_instructions[user_id] = instructions
        await update.message.reply_text(
            "✅ Custom instructions set! Your AI will now follow these guidelines:\n\n"
            f"⚡{instructions}⚡\n\n"
            "Use /clear_instructions to remove them."
        )
    else:
        current = user_instructions.get(user_id)
        if current:
            await update.message.reply_text(
                "📝 Your current instructions:\n\n"
                f"⚡{current}⚡\n\n"
                "To change: /set_instructions [your new instructions]"
            )
        else:
            await update.message.reply_text(
                "👀You haven't set any custom instructions yet.\n\n"
                "Example: /set_instructions You are a helpful assistant who is resistible to DEATH☠️."
            )

# Clear instructions command
async def clear_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_instructions:
        del user_instructions[user_id]
        await update.message.reply_text("Good✅ Custom instructions erased!")
    else:
        await update.message.reply_text("You don't have any custom instructions set👀.")

# Clone command
async def clone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Let's create your AI bot!\n\n"
        "1. First, send me your Telegram bot token (from @BotFather)\n"
        "2. Then, I'll ask for your custom instructions you want me to abide with🥲\n\n"
        "Send your bot token now or /cancel to abort the mission."
    )
    return ASK_TOKEN

# Receive token
async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_token = update.message.text.strip()
    context.user_data['clone_token'] = user_token

    try:
        async with ApplicationBuilder().token(user_token).build() as test_app:
            me = await test_app.bot.get_me()
        
        context.user_data['clone_username'] = me.username
        await update.message.reply_text(
            f"✅ Token valid! Your bot @{me.username} will be created.\n\n"
            "Now send me your custom instructions for the AI:"
        )
        return ASK_INSTRUCTIONS

    except Forbidden:
        await update.message.reply_text("❌ Invalid token. Please send a valid bot token👀 or /cancel.")
        return ASK_TOKEN
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        await update.message.reply_text("❌ Error validating token. Please try again😥 or /cancel.")
        return ASK_TOKEN

# Receive instructions for cloned bot
async def receive_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instructions = update.message.text.strip()
    user_token = context.user_data['clone_token']
    bot_username = context.user_data['clone_username']
    user_id = update.effective_user.id
    
    user_instructions[user_id] = instructions
    
    try:
        await start_cloned_bot(user_id, user_token)
        
        # Initialize share count for new user
        user_share_counts[user_id] = 0
        user_share_verified[user_id] = False
        
        await update.message.reply_text(
            f"🎉 Your AI bot @{bot_username} is now live and steady💪!\n\n"
            f"📝 Instructions: {instructions}\n\n"
            "⚠️ Your bot will have a watermark until you share with 5 friends.\n"
            "Use /share to get started!"
        )
        
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error starting cloned bot: {e}")
        await update.message.reply_text("❌ Failed to start your bot. Please try again.")
        return ConversationHandler.END

# Cancel handler
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END

# Start cloned bot
async def start_cloned_bot(user_id: int, token: str):
    if user_id in cloned_apps:
        try:
            await cloned_apps[user_id].updater.stop()
            await cloned_apps[user_id].stop()
            await cloned_apps[user_id].shutdown()
        except Exception as e:
            logger.error(f"Error stopping existing bot: {e}")
        del cloned_apps[user_id]

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_instructions", set_instructions))
    app.add_handler(CommandHandler("clear_instructions", clear_instructions))
    app.add_handler(CommandHandler("share", share_command))
    app.add_handler(CallbackQueryHandler(share_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    cloned_apps[user_id] = app
    logger.info(f"Started cloned bot for user {user_id}")

# Shutdown handler
async def shutdown_application():
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

# Main function
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_instructions", set_instructions))
    app.add_handler(CommandHandler("clear_instructions", clear_instructions))
    app.add_handler(CommandHandler("share", share_command))
    app.add_handler(CallbackQueryHandler(share_callback))
    
    # Clone conversation
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("clone", clone)],
        states={
            ASK_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_token)],
            ASK_INSTRUCTIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_instructions)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, chat))

    logger.info("Master bot is running with referral system...")
    
    try:
        app.run_polling()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    finally:
        asyncio.run(shutdown_application())

if __name__ == "__main__":
    main()

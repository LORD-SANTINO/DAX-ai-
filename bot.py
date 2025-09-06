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

# Store active cloned apps and their instructions
cloned_apps = {}
user_instructions = {}  # {user_id: "custom instructions"}

# NEW: Referral system storage
user_referrals = {}  # {user_id: {'count': 0, 'verified': False}}
referral_codes = {}  # {referral_code: user_id}
referral_users = {}  # {new_user_id: referrer_id} to track who referred whom

# Start command - UPDATED WITH REFERRAL CHECKING
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user_{user_id}"
    
    # Check if this is a referral join
    if context.args and context.args[0].startswith('ref_'):
        referral_code = context.args[0]
        await handle_referral(update, context, referral_code, user_id, username)
        return
    
    # Check if user needs to share (has cloned bot but not enough referrals)
    if user_id in user_referrals and not user_referrals[user_id]['verified']:
        remaining = 5 - user_referrals[user_id]['count']
        await update.message.reply_text(
            f"📣 Share with {remaining} more people to remove the watermark!\n\n"
            "Use /share to get your referral link and instructions."
        )
        return
    
    await update.message.reply_text(
        "🤖 Hello! I'm your AI bot (GPT-powered). Send me a message!\n\n"
        "Use /clone to create your own AI bot with your own custom instructions!"
    )

# NEW: Handle real referrals
async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE, referral_code: str, new_user_id: int, new_username: str):
    if referral_code in referral_codes:
        referrer_id = referral_codes[referral_code]
        
        # Check if this new user was already referred by someone
        if new_user_id not in referral_users:
            referral_users[new_user_id] = referrer_id
            
            # Add to referrer's count if they have a referral record
            if referrer_id in user_referrals:
                user_referrals[referrer_id]['count'] += 1
                logger.info(f"User {referrer_id} got a referral from {new_user_id}. Total: {user_referrals[referrer_id]['count']}")
                
                # Notify the referrer
                try:
                    remaining = 5 - user_referrals[referrer_id]['count']
                    if remaining > 0:
                        await context.bot.send_message(
                            referrer_id,
                            f"🎉 @{new_username} joined using your referral link!\n"
                            f"📊 You need {remaining} more referrals to remove the watermark."
                        )
                    else:
                        user_referrals[referrer_id]['verified'] = True
                        await context.bot.send_message(
                            referrer_id,
                            "✨ Premium Experience Unlocked! ✨\n\n"
                            "🎊 Thank you for sharing the love!\n"
                            "✅ The watermark has been removed from your bot\n"
                            "🌟 Your AI responses will now appear clean & professional\n\n"
                            "Thank you for being an amazing part of our community!_💫"
                        )
                except Exception as e:
                    logger.error(f"Could not notify referrer {referrer_id}: {e}")
        
        # Welcome the new user
        await update.message.reply_text(
            f"👋 Welcome! You joined through a friend's referral.\n\n"
            "This bot lets you create AI assistants with custom personalities!\n\n"
            "Use /clone to create your own AI bot or just start chatting! 🚀"
        )
    else:
        await update.message.reply_text(
            "🤖 Hello! Welcome to the DAX AI bot experience!\n\n"
            "Use /clone to create your own AI assistant with custom instructions!"
        )

# NEW: Share command with REAL referral system
async def share_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user_{user_id}"
    
    # Check if user has cloned a bot (only cloned bot users need referrals)
    if user_id not in cloned_apps:
        await update.message.reply_text(
            "⚠️ You need to create your own bot first using /clone to use the referral system!👀"
        )
        return
    
    # Generate unique referral code
    referral_code = f"ref_{user_id}_{os.urandom(4).hex()}"
    referral_codes[referral_code] = user_id
    
    # Initialize user referrals if not exists
    if user_id not in user_referrals:
        user_referrals[user_id] = {'count': 0, 'verified': False}
    
    referral_link = f"https://t.me/daxotp_bot?start={referral_code}"
    remaining = 5 - user_referrals[user_id]['count']
    
    await update.message.reply_text(
        f"📣 Referral Program**\n\n"
        f"🔗 Your unique link: `{referral_link}`\n\n"
        f"📊 Progress: {user_referrals[user_id]['count']}/5 referrals\n"
        f"🎯 Remaining**: {remaining} more to remove watermark\n\n"
        "How it works:\n"
        "• Share your unique link with friends\n"
        "• When they join using your link, it counts\n"
        "• After 5 real joins, watermark disappears,!\n\n"
        "✨ No fake clicks - only real joins count!",
        parse_mode='Markdown'
    )

# Enhanced chat handler with REAL watermark system
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if model is None:
        await update.message.reply_text("⚠️ Bot is not properly configured. Please contact the administrator.@dn_feedbackbot")
        return
        
    user_message = update.message.text
    user_id = update.effective_user.id
    
    try:
        # Check if this user has custom instructions
        instructions = user_instructions.get(user_id, "")
        
        # Create enhanced prompt with custom instructions
        enhanced_prompt = f"{instructions}\n\nUser: {user_message}" if instructions else user_message
        
        response = model.generate_content(enhanced_prompt)
        response_text = response.text
        
        # Add watermark if user has cloned a bot but hasn't reached 5 REAL referrals
        if user_id in cloned_apps and (user_id not in user_referrals or not user_referrals[user_id].get('verified', False)):
            remaining = 5 - user_referrals.get(user_id, {'count': 0})['count']
            watermark = f"\n\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n🔹 Cloned by @daxotp_bot\n📊 {remaining} referrals needed to remove"
            response_text += watermark
        
        await update.message.reply_text(response.text)

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
        # Save instructions
        instructions = " ".join(context.args)
        user_instructions[user_id] = instructions
        await update.message.reply_text(
            "✅ Custom instructions set! Your AI will now follow these guidelines:\n\n"
            f"⚡{instructions}⚡\n\n"
            "Use /clear_instructions to remove them."
        )
    else:
        # Show current instructions
        current = user_instructions.get(user_id)
        if current:
            await update.message.reply_text(
                "📝 Your current instructions:\n\n"
                f"{current}\n\n"
                "To change: /set_instructions [your new instructions]"
            )
        else:
            await update.message.reply_text(
                "You haven't set any custom instructions yet.👀\n\n"
                "Example: /set_instructions You are a helpful assistant who is irresistible to DEATH"
            )

# Clear instructions command
async def clear_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_instructions:
        del user_instructions[user_id]
        await update.message.reply_text("✅ Custom instructions successfully erased!")
    else:
        await update.message.reply_text("You don't have any custom instructions set.🥲")

# Clone command with instructions
async def clone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Let's create your AI bot!\n\n"
        "1. First, send me your Telegram bot token (from @BotFather)\n"
        "2. Then, I'll ask for your custom instructions for me to sbide with.\n\n"
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
            "Now send me your custom instructions for the AI (e.g., 'You are DEATH himself'):"
        )
        return ASK_INSTRUCTIONS

    except Forbidden:
        await update.message.reply_text("❌ Invalid token. Please send a valid bot token or /cancel.")
        return ASK_TOKEN
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        await update.message.reply_text("❌ Error validating token. Please try again or /cancel.")
        return ASK_TOKEN

# Receive instructions for cloned bot
async def receive_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instructions = update.message.text.strip()
    user_token = context.user_data['clone_token']
    bot_username = context.user_data['clone_username']
    
    # Save instructions for this cloned bot
    user_id = update.effective_user.id
    user_instructions[user_id] = instructions
    
    try:
        # Start the cloned bot
        await start_cloned_bot(user_id, user_token)
        
        # Initialize referral tracking for this user
        user_referrals[user_id] = {'count': 0, 'verified': False}
        
        await update.message.reply_text(
            f"🎉 Your AI bot @{bot_username} is now live and steady💪!\n\n"
            f"📝 Instructions: _{instructions}_\n\n"
            "⚠️ Your bot will have a watermark until you share with 5 friends.\n"
            "Use /share to get your referral link and remove the watermark!"
        )
        
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"Error starting cloned bot: {e}")
        await update.message.reply_text("❌ Failed to start your bot😥. Please try again.")
        return ConversationHandler.END

# Cancel handler
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled❌.")
    return ConversationHandler.END

# Start cloned bot with custom instructions
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
    
    # Enhanced clone conversation
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

    logger.info("Master bot is running with REAL referral system...")
    
    try:
        app.run_polling()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    finally:
        asyncio.run(shutdown_application())

if __name__ == "__main__":
    main()

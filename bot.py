import os
import logging
import asyncio
import sys
import subprocess
import google.generativeai as genai
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from telegram.error import Forbidden

# local DB helpers
from db import save_clone, list_active_clones, get_clone, increment_referral, get_referral, REFERRAL_THRESHOLD

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Keep your GEMINI key rotation & configure_gemini() code here...
GEMINI_API_KEYS = [key for key in [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
] if key is not None]

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

ASK_TOKEN, ASK_INSTRUCTIONS = range(2)

# in-memory caches still used for quick lookups (optional)
cloned_apps = {}
user_instructions = {}
user_referrals = {}
referral_codes = {}
referral_users = {}

# Start command (same as before)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user_{user_id}"
    if context.args and context.args[0].startswith('ref_'):
        referral_code = context.args[0]
        await handle_referral(update, context, referral_code, user_id, username)
        return
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

async def handle_referral(update: Update, context: ContextTypes.DEFAULT_TYPE, referral_code: str, new_user_id: int, new_username: str):
    if referral_code in referral_codes:
        referrer_id = referral_codes[referral_code]
        # Avoid counting the same join multiple times; track in referral_users (in-memory)
        if new_user_id not in referral_users:
            referral_users[new_user_id] = referrer_id
            # Persist increment and get updated row
            try:
                ref_row = increment_referral(referrer_id)
            except Exception as e:
                logger.error(f"Failed to increment persisted referral for {referrer_id}: {e}")
                ref_row = get_referral(referrer_id) or {"count": 0, "verified": False}

            # Update in-memory copy for quick messaging and compatibility
            if referrer_id not in user_referrals:
                user_referrals[referrer_id] = {'count': ref_row.get("count", 0), 'verified': ref_row.get("verified", False)}
            else:
                user_referrals[referrer_id]['count'] = ref_row.get("count", 0)
                user_referrals[referrer_id]['verified'] = ref_row.get("verified", False)

            try:
                remaining = max(0, REFERRAL_THRESHOLD - ref_row.get("count", 0))
                if not ref_row.get("verified", False) and ref_row.get("count", 0) >= REFERRAL_THRESHOLD:
                    user_referrals[referrer_id]['verified'] = True
                    await context.bot.send_message(
                        referrer_id,
                        "✨ Premium Experience Unlocked! ✨\n\n🎊 Thank you for sharing!\n✅ The watermark has been removed from your bot."
                    )
                else:
                    await context.bot.send_message(
                        referrer_id,
                        f"🎉 @{new_username} joined using your referral link!\n📊 You now have {ref_row.get('count',0)} referrals. {remaining} more to remove the watermark."
                    )
            except Exception as e:
                logger.error(f"Could not notify referrer {referrer_id}: {e}")

        await update.message.reply_text(
            f"👋 Welcome! You joined through a friend's referral.\n\nUse /clone to create your own AI bot or just start chatting! 🚀"
        )
    else:
        # default greeting when no valid referral
        await update.message.reply_text(
            "🤖 Hello! Welcome to the DAX AI bot experience!\n\nUse /clone to create your own AI assistant with custom instructions!"
)

async def share_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or f"user_{user_id}"
    if user_id not in cloned_apps and user_id not in [c['user_id'] for c in list_active_clones()]:
        await update.message.reply_text(
            "⚠️ You need to create your own bot first using /clone to use the referral system!👀"
        )
        return
    referral_code = f"ref_{user_id}_{os.urandom(4).hex()}"
    referral_codes[referral_code] = user_id
    if user_id not in user_referrals:
        user_referrals[user_id] = {'count': 0, 'verified': False}
    master_username = (await context.bot.get_me()).username
    referral_link = f"https://t.me/{master_username}?start={referral_code}"
    remaining = 5 - user_referrals[user_id]['count']
    await update.message.reply_text(
        f"📣 Referral Program\n\n"
        f"🔗 Your unique link: {referral_link}\n\n"
        f"📊 Progress: {user_referrals[user_id]['count']}/5 referrals\n"
        f"🎯 Remaining: {remaining} more to remove watermark\n\n"
        "How it works:\n"
        "• Share your unique link with friends\n"
        "• When they join using your link, it counts\n"
        "• After 5 real joins, watermark disappears\n\n"
        "✨ No fake clicks - only real joins count!"
    )

# Chat handler (uses model) - kept simple; use asyncio.to_thread if model is blocking
async def chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if model is None:
        await update.message.reply_text("⚠️ Bot is not properly configured. Please contact the administrator.")
        return
    user_message = update.message.text or ""
    user_id = update.effective_user.id
    try:
        instructions = user_instructions.get(user_id, "")
        enhanced_prompt = f"{instructions}\n\nUser: {user_message}" if instructions else user_message

        # If model.generate_content is blocking, run in thread
        resp = await asyncio.to_thread(model.generate_content, enhanced_prompt)
        response_text = getattr(resp, "text", str(resp))

        if user_id in [c['user_id'] for c in list_active_clones()] and (
            user_id not in user_referrals or not user_referrals[user_id].get("verified", False)
        ):
            ref_data = user_referrals.get(user_id, {})
            remaining = 5 - ref_data.get("count", 0)
            watermark = (
                "\n\n┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈"
                "\n🔹 Cloned by @daxotp_bot"
                f"\n📊 {remaining} referrals needed to remove"
            )
            response_text += watermark

        await update.message.reply_text(response_text)
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            switch_key()
            await update.message.reply_text("⚠️ Quota exceeded, switching API key... Please try again.")
        else:
            logger.error(f"Error: {e}")
            await update.message.reply_text("⚠️ Sorry, I encountered an error processing your request.")

def switch_key():
    global current_key_index
    current_key_index = (current_key_index + 1) % len(GEMINI_API_KEYS)
    configure_gemini()
    logger.warning(f"Switched to API key #{current_key_index + 1}")

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
                f"{current}\n\n"
                "To change: /set_instructions [your new instructions]"
            )
        else:
            await update.message.reply_text(
                "You haven't set any custom instructions yet.👀\n\n"
                "Example: /set_instructions You are a helpful assistant who is irresistible to DEATH"
            )

async def clear_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id in user_instructions:
        del user_instructions[user_id]
        await update.message.reply_text("✅ Custom instructions successfully erased!")
    else:
        await update.message.reply_text("You don't have any custom instructions set.🥲")

# Conversation for /clone remains but now we persist and spawn a worker instead of in-process start
async def clone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 Let's create your AI bot!\n\n"
        "1. First, send me your Telegram bot token (from @BotFather)\n"
        "2. Then, I'll ask for your custom instructions\n\n"
        "Send your bot token now or /cancel to abort."
    )
    return ASK_TOKEN

async def receive_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_token = update.message.text.strip()
    context.user_data['clone_token'] = user_token

    try:
        # Validate token by creating a Bot and calling get_me (no polling)
        from telegram import Bot
        test_bot = Bot(token=user_token)
        me = await test_bot.get_me()
        context.user_data['clone_username'] = me.username
        await update.message.reply_text(
            f"✅ Token valid! Your bot @{me.username} will be created.\n\n"
            "Now send me your custom instructions for the AI:"
        )
        return ASK_INSTRUCTIONS
    except Forbidden:
        await update.message.reply_text("❌ Invalid token. Please send a valid bot token or /cancel.")
        return ASK_TOKEN
    except Exception as e:
        logger.error(f"Error validating token: {e}")
        await update.message.reply_text("❌ Error validating token. Please try again or /cancel.")
        return ASK_TOKEN

def spawn_clone_worker(user_id: int):
    logger = logging.getLogger(__name__)
    env = os.environ.copy()
    env["CLONE_USER_ID"] = str(user_id)
    # DB_PATH and MASTER_KEY must already be present in env
    if "MASTER_KEY" not in env:
        logger.error("MASTER_KEY missing when attempting to spawn worker")
        raise RuntimeError("MASTER_KEY missing")
    python = sys.executable
    worker_script = os.path.join(os.path.dirname(__file__), "clone_worker.py")
    logger.info("Spawning clone worker for user %s with script %s", user_id, worker_script)
    proc = subprocess.Popen([python, worker_script], env=env, close_fds=True)
    logger.info("Spawned pid %s for clone %s", proc.pid, user_id)
    return proc

async def receive_instructions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    instructions = update.message.text.strip()
    user_token = context.user_data['clone_token']
    bot_username = context.user_data['clone_username']
    user_id = update.effective_user.id
    user_instructions[user_id] = instructions
    try:
        # Persist clone metadata (encrypts token)
        save_clone(user_id, user_token, bot_username, instructions)

        # Spawn a detached worker process that runs the cloned bot
        spawn_clone_worker(user_id)

        # Initialize referral tracking for this user
        user_referrals[user_id] = {'count': 0, 'verified': False}

        await update.message.reply_text(
            f"🎉 Your AI bot @{bot_username} is now live!\n\n"
            f"📝 Instructions: _{instructions}_\n\n"
            "⚠️ Your bot will have a watermark until you share with 5 friends.\n"
            "Use /share to get your referral link and remove the watermark!"
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error starting cloned bot: {e}")
        await update.message.reply_text("❌ Failed to start your bot😥. Please try again.")
        return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled❌.")
    return ConversationHandler.END

async def shutdown_application():
    logger.info("Shutting down all cloned bots (master manages only spawning).")
    # master no longer manages in-process clone apps; workers run independently

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_instructions", set_instructions))
    app.add_handler(CommandHandler("clear_instructions", clear_instructions))
    app.add_handler(CommandHandler("share", share_command))
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

    # On startup, respawn active clones from DB
    try:
        active = list_active_clones()
        for clone_rec in active:
            uid = clone_rec["user_id"]
            try:
                spawn_clone_worker(uid)
            except Exception as e:
                logging.error(f"Failed to spawn worker for {uid}: {e}")
    except Exception as e:
        logging.error(f"Failed to load active clones from DB: {e}")

    logger.info("Master bot is running (with persistent clones)...")
    try:
        app.run_polling()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    finally:
        # workers are independent; master does not own their lifecycle here.
        pass

if __name__ == "__main__":
    main()

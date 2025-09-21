import os
import asyncio
import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from telegram.error import Forbidden, BadRequest, RetryAfter, TelegramError

from user_db_helpers import insert_or_update_user, get_all_subscribed_user_ids, remove_user, get_user_count, opt_out_user

logger = logging.getLogger(__name__)

# Admin id (set as env var ADMIN_USER_ID to your Telegram id)
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

# --- Track users handler ---
async def track_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Call this on incoming private-chat messages (recommended).
    Example registration:
      app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.ALL, track_user), group=0)
    """
    user = update.effective_user
    if not user:
        return
    try:
        insert_or_update_user(user.id, user.username)
        logger.debug("Tracked user %s (@%s)", user.id, user.username)
    except Exception as e:
        logger.exception("Failed to track user: %s", e)

# --- Admin: user count ---
async def user_count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender = update.effective_user.id
    if sender != ADMIN_USER_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return
    try:
        total = get_user_count(include_opted_out=False)
        await update.message.reply_text(f"Subscribed users: {total}")
    except Exception as e:
        logger.exception("Failed to get user count: %s", e)
        await update.message.reply_text("❌ Failed to fetch user count. Check logs.")

# --- User opt-out (optional, good practice) ---
async def user_optout_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    try:
        opt_out_user(user.id)
        await update.message.reply_text("You have been unsubscribed from broadcasts. Message the bot again to re-subscribe.")
    except Exception as e:
        logger.exception("Failed to opt-out user %s: %s", user.id, e)
        await update.message.reply_text("Failed to process your request. Try again later.")

# --- Admin: broadcast command ---
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage (admin only):
      /broadcast Your message here...
    Sends the message to all saved users (those who have interacted and are not opted out).
    """
    sender = update.effective_user.id
    if sender != ADMIN_USER_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /broadcast Your message here")
        return

    text = " ".join(context.args)
    user_ids = get_all_subscribed_user_ids()
    if not user_ids:
        await update.message.reply_text("No subscribed users found.")
        return

    await update.message.reply_text(f"Starting broadcast to {len(user_ids)} users...")

    # Throttle and concurrency settings - adjust as needed for safety
    concurrency = int(os.getenv("BROADCAST_CONCURRENCY", "8"))
    delay_between_sends = float(os.getenv("BROADCAST_DELAY", "0.05"))  # small delay to avoid bursts

    sem = asyncio.Semaphore(concurrency)
    stats = {"sent": 0, "failed": 0, "removed": 0}

    async def send_to(uid: int):
        async with sem:
            try:
                # slight delay to reduce bursts
                await asyncio.sleep(delay_between_sends)
                await context.bot.send_message(chat_id=uid, text=text)
                stats["sent"] += 1
            except RetryAfter as e:
                wait = e.retry_after if hasattr(e, "retry_after") else 5
                logger.warning("RetryAfter for %s, sleeping %s sec", uid, wait)
                await asyncio.sleep(wait)
                # one retry
                try:
                    await context.bot.send_message(chat_id=uid, text=text)
                    stats["sent"] += 1
                except Exception as e2:
                    logger.exception("Retry failed for %s: %s", uid, e2)
                    stats["failed"] += 1
            except Forbidden as e:
                logger.info("Bot blocked by user %s, removing.", uid)
                remove_user(uid)
                stats["removed"] += 1
            except BadRequest as e:
                # chat not found or bad message
                logger.info("BadRequest for %s: %s — removing", uid, e)
                remove_user(uid)
                stats["removed"] += 1
            except TelegramError as e:
                logger.exception("TelegramError sending to %s: %s", uid, e)
                stats["failed"] += 1
            except Exception as e:
                logger.exception("Unexpected error sending to %s: %s", uid, e)
                stats["failed"] += 1

    tasks = [asyncio.create_task(send_to(uid)) for uid in user_ids]
    await asyncio.gather(*tasks)

    await update.message.reply_text(
        f"Broadcast finished. Sent: {stats['sent']}, Failed: {stats['failed']}, Removed: {stats['removed']}"
    )

# Utility: return handlers for easy registration
def get_handlers():
    return [
        MessageHandler(filters.ChatType.PRIVATE & filters.ALL, track_user),
        CommandHandler("broadcast", broadcast_cmd),
        CommandHandler("user_count", user_count_cmd),
        CommandHandler("stop", user_optout_cmd),
    ]

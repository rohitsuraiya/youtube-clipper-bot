import os
import sys
import logging

def _patch_apscheduler():
    try:
        import apscheduler.util
        import pytz
        _orig = apscheduler.util.astimezone
        def _fixed(tz):
            if tz is None:
                return pytz.utc
            if hasattr(tz, 'key'):
                return pytz.timezone(tz.key)
            return _orig(tz)
        apscheduler.util.astimezone = _fixed
    except Exception:
        pass

_patch_apscheduler()

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from bot import BOT_TOKEN
from bot.jobs import init_db, save_job, get_user_jobs
import bot.clip as clip


logger = logging.getLogger(__name__)


async def safe_edit(query, text, parse_mode='html', reply_markup=None):
    try:
        await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception:
        try:
            await query.answer()
        except Exception:
            pass
        try:
            msg = query.message
            if msg.photo or msg.video or msg.document:
                await msg.delete()
                await query.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception:
            pass


def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📹 Create Clips", callback_data="create_clips")],
        [InlineKeyboardButton("📂 My Jobs", callback_data="my_jobs"),
         InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
        [InlineKeyboardButton("🆘 Help", callback_data="help")],
    ])


def style_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Viral Shorts", callback_data="style_viral")],
        [InlineKeyboardButton("🎓 Educational", callback_data="style_educational")],
        [InlineKeyboardButton("😂 Funny Moments", callback_data="style_funny")],
        [InlineKeyboardButton("🎮 Gaming", callback_data="style_gaming")],
        [InlineKeyboardButton("💰 Business", callback_data="style_business")],
        [InlineKeyboardButton("🤖 AI Auto", callback_data="style_ai")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])


def quality_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("480p", callback_data="quality_480"),
         InlineKeyboardButton("720p", callback_data="quality_720")],
        [InlineKeyboardButton("1080p", callback_data="quality_1080"),
         InlineKeyboardButton("4K", callback_data="quality_4k")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])


def length_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Auto Detect", callback_data="length_auto")],
        [InlineKeyboardButton("15-30 sec", callback_data="length_15_30")],
        [InlineKeyboardButton("30-60 sec", callback_data="length_30_60")],
        [InlineKeyboardButton("60-90 sec", callback_data="length_60_90")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ])


def face_tracking_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Enabled", callback_data="face_on"),
         InlineKeyboardButton("❌ Disabled", callback_data="face_off")],
    ])


def format_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 9:16 Shorts", callback_data="format_916")],
        [InlineKeyboardButton("🖥️ 16:9 YouTube", callback_data="format_169")],
        [InlineKeyboardButton("📷 1:1 Square", callback_data="format_11")],
    ])


def auto_reframe_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes - Smart Auto Reframe", callback_data="reframe_on")],
        [InlineKeyboardButton("❌ No - Basic Crop Only", callback_data="reframe_off")],
    ])


def confirm_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Start Processing", callback_data="start_job")],
        [InlineKeyboardButton("✏️ Edit Settings", callback_data="create_clips")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_job")],
    ])


def summary_text(draft):
    return (
        "📋 <b>Processing Summary</b>\n\n"
        f"🔗 <b>Video:</b> {draft.get('url', '—')}\n"
        f"🎬 <b>Style:</b> {draft.get('style', '—')}\n"
        f"⚡ <b>Quality:</b> {draft.get('quality', '—')}\n"
        f"⏱ <b>Length:</b> {draft.get('length', '—')}\n"
        f"👤 <b>Face Tracking:</b> {'✅ On' if draft.get('face_tracking') else '❌ Off'}\n"
        f"🔄 <b>Auto Reframe:</b> {'✅ On' if draft.get('auto_reframe') else '❌ Off'}\n"
        f"📱 <b>Format:</b> {draft.get('format', '—')}\n\n"
        "Ready to start?"
    )


STYLE_MAP = {
    "style_viral": "🔥 Viral Shorts",
    "style_educational": "🎓 Educational",
    "style_funny": "😂 Funny Moments",
    "style_gaming": "🎮 Gaming",
    "style_business": "💰 Business",
    "style_ai": "🤖 AI Auto",
}

QUALITY_MAP = {
    "quality_480": "480p",
    "quality_720": "720p",
    "quality_1080": "1080p",
    "quality_4k": "4K",
}

LENGTH_MAP = {
    "length_auto": "⚡ Auto Detect",
    "length_15_30": "15-30 sec",
    "length_30_60": "30-60 sec",
    "length_60_90": "60-90 sec",
}

FORMAT_MAP = {
    "format_916": "📱 9:16 Shorts",
    "format_169": "🖥️ 16:9 YouTube",
    "format_11": "📷 1:1 Square",
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    image_path = os.path.join(os.path.dirname(__file__), '..', 'media', 'welcome.jpg')
    text = (
        "🎬 <b>Welcome to AutoCut AI Clipper!</b>\n\n"
        "I turn long videos into viral short clips using AI.\n\n"
        "Choose an option below:"
    )
    if os.path.exists(image_path):
        with open(image_path, 'rb') as f:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=f,
                caption=text,
                parse_mode='html',
                reply_markup=main_menu_keyboard()
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            parse_mode='html',
            reply_markup=main_menu_keyboard()
        )


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await safe_edit(query, "🏠 <b>Main Menu</b>\n\nChoose an option:",
                    reply_markup=main_menu_keyboard())


async def create_clips_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['step'] = 'url'
    text = (
        "📹 <b>Paste your YouTube URL</b>\n\n"
        "Example:\n"
        "<code>https://youtube.com/watch?v=xxxxx</code>\n"
        "<code>https://youtu.be/xxxxx</code>"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
    ])
    await safe_edit(query, text, reply_markup=kb)


async def my_jobs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    jobs = get_user_jobs(user_id, limit=5)

    if not jobs:
        await safe_edit(query,
            "📂 <b>My Jobs</b>\n\nNo jobs yet. Use 📹 Create Clips to get started!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📹 Create Clips", callback_data="create_clips")],
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
            ]))
        return

    status_emoji = {
        'pending': '⏳', 'processing': '⚙️',
        'completed': '✅', 'failed': '❌'
    }
    lines = []
    for j in jobs:
        emoji = status_emoji.get(j['status'], '❓')
        lines.append(f"{emoji} #{j['id']} — {j['style']} — {j['status'].upper()}")

    await safe_edit(query,
        f"📂 <b>Recent Jobs</b>\n\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📹 Create New Clips", callback_data="create_clips")],
            [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
        ]))


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query,
        "⚙️ <b>Settings</b>\n\nDefault settings are configured.\nPremium users get advanced options.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
        ]))


async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query,
        "🆘 <b>Help</b>\n\n"
        "<b>How it works:</b>\n"
        "1. Click 📹 Create Clips\n"
        "2. Paste a YouTube URL\n"
        "3. Choose your settings\n"
        "4. AI finds the best moments\n"
        "5. Download your viral clips!\n\n"
        "<b>Commands:</b>\n"
        "/start — Main menu\n\n"
        "Need help? Contact @admin",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
        ]))


async def style_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data in STYLE_MAP:
        context.user_data['style'] = STYLE_MAP[data]
        context.user_data['step'] = 'quality'
        await safe_edit(query, "⚡ <b>Select Video Quality</b>",
                        reply_markup=quality_keyboard())


async def quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data in QUALITY_MAP:
        context.user_data['quality'] = QUALITY_MAP[data]
        context.user_data['step'] = 'length'
        await safe_edit(query, "⏱ <b>Choose Clip Length</b>",
                        reply_markup=length_keyboard())


async def length_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data in LENGTH_MAP:
        context.user_data['length'] = LENGTH_MAP[data]
        context.user_data['step'] = 'face'
        await safe_edit(query,
            "👤 <b>Face Tracking?</b>\n\nKeeps the speaker centered in frame.",
            reply_markup=face_tracking_keyboard())


async def face_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['face_tracking'] = query.data == 'face_on'
    context.user_data['step'] = 'reframe'
    await safe_edit(query,
        "🔄 <b>Auto Reframe?</b>\n\nSmart reframing keeps the speaker's eyes in the upper third and follows movement. Disable for basic center crop.",
        reply_markup=auto_reframe_keyboard())


async def format_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data in FORMAT_MAP:
        context.user_data['format'] = FORMAT_MAP[data]
        context.user_data['step'] = 'confirm'
        await safe_edit(query, summary_text(context.user_data),
                        reply_markup=confirm_keyboard())


async def reframe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['auto_reframe'] = query.data == 'reframe_on'
    context.user_data['step'] = 'format'
    await safe_edit(query, "📱 <b>Output Format</b>",
                    reply_markup=format_keyboard())


async def start_job_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🚀 Starting...")
    await clip.start_processing(update, context)


async def cancel_job_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Cancelled")
    context.user_data.clear()
    await safe_edit(query, "❌ Cancelled. What would you like to do?",
                    reply_markup=main_menu_keyboard())


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    step = context.user_data.get('step')

    if step == 'url':
        import re
        url = update.message.text.strip()
        yt_regex = re.compile(
            r'(https?://)?(www\.)?(youtube\.com/(watch\?v=|live/)|youtu\.be/|youtube\.com/shorts/)[^\s]+'
        )
        if not yt_regex.match(url):
            await update.message.reply_text(
                "⚠️ That doesn't look like a valid YouTube URL.\n"
                "Please paste a full URL starting with http:// or https://",
            )
            return
        context.user_data['url'] = url
        context.user_data['step'] = 'style'
        await update.message.reply_text(
            "🎬 <b>Choose Clip Style</b>",
            parse_mode='html',
            reply_markup=style_keyboard()
        )
        return

    await update.message.reply_text(
        "Use the menu to navigate:",
        reply_markup=main_menu_keyboard()
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling update:", exc_info=context.error)


def main():
    os.makedirs('outputs', exist_ok=True)
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()

    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', start))

    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern='^main_menu$'))
    app.add_handler(CallbackQueryHandler(create_clips_callback, pattern='^create_clips$'))
    app.add_handler(CallbackQueryHandler(my_jobs_callback, pattern='^my_jobs$'))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern='^settings$'))
    app.add_handler(CallbackQueryHandler(help_callback, pattern='^help$'))

    app.add_handler(CallbackQueryHandler(style_callback, pattern='^style_'))
    app.add_handler(CallbackQueryHandler(quality_callback, pattern='^quality_'))
    app.add_handler(CallbackQueryHandler(length_callback, pattern='^length_'))
    app.add_handler(CallbackQueryHandler(face_callback, pattern='^face_'))
    app.add_handler(CallbackQueryHandler(reframe_callback, pattern='^reframe_'))
    app.add_handler(CallbackQueryHandler(format_callback, pattern='^format_'))

    app.add_handler(CallbackQueryHandler(start_job_callback, pattern='^start_job$'))
    app.add_handler(CallbackQueryHandler(cancel_job_callback, pattern='^cancel_job$'))
    app.add_handler(CallbackQueryHandler(clip.handle_clip_count, pattern='^clipcount_'))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Bot is running...")
    app.run_polling()


if __name__ == '__main__':
    main()

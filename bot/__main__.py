import os
import sys

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

from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot import BOT_TOKEN
import bot.clip as clip


async def start(update, context):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "<b>YouTube Clipper Bot</b>\n\n"
            "<b>Auto Mode</b> - Send just a YouTube link and I'll detect highlight moments automatically.\n\n"
            "<b>Manual Mode</b> - Send a link with start and end times for a custom clip.\n\n"
            "<b>Format:</b>\n"
            "<code>&lt;YouTube URL&gt; &lt;start&gt; &lt;end&gt;</code>\n\n"
            "<b>Examples:</b>\n"
            "<code>https://youtu.be/dQw4w9WgXcQ</code>  (auto)\n"
            "<code>https://youtu.be/dQw4w9WgXcQ 1:30 2:45</code>  (manual)\n\n"
            "Times can be in <code>M:SS</code> or <code>MM:SS</code> or <code>HH:MM:SS</code> format."
        ),
        parse_mode='html'
    )


def main():
    os.makedirs('outputs', exist_ok=True)
    app = ApplicationBuilder().token(BOT_TOKEN).job_queue(None).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', start))
    app.add_handler(CallbackQueryHandler(clip.quality_callback, pattern='^quality_'))
    app.add_handler(CallbackQueryHandler(clip.handle_clip_count, pattern='^clipcount_'))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, clip.handle_message))
    print("Bot is running...")
    app.run_polling()


if __name__ == '__main__':
    main()

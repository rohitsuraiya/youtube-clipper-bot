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
    video_path = os.path.join(os.path.dirname(__file__), '..', 'media', 'welcome.mp4')
    text = (
        "✂️ <b>AutoCut - AI Video Clipper</b>\n\n"
        "🧠 <b>AI-powered highlight detection</b> (Whisper + GPT)\n"
        "🎯 <b>Non-linear clips</b> — best moments first\n"
        "⏱️ <b>Variable length</b> — 15-60s based on content\n"
        "📱 <b>9:16 smart crop</b> for Shorts/Reels\n"
        "📝 <b>YouTube SEO</b> — title, description, hashtags\n"
        "⚡ <b>Quality selection</b> — 480p / 720p / 1080p / 4K\n\n"
        "🤖 <b>Auto Mode</b> — Send a YouTube link, AI finds the best clips\n\n"
        "🛠 <b>Manual Mode</b> — Send link with timestamps for a custom clip\n\n"
        "💡 <b>Format:</b> <code>&lt;URL&gt; &lt;start&gt; &lt;end&gt;</code>\n\n"
        "📌 <b>Examples:</b>\n"
        "<code>https://youtu.be/dQw4w9WgXcQ</code>  (auto)\n"
        "<code>https://youtu.be/dQw4w9WgXcQ 1:30 2:45</code>  (manual)\n\n"
        "⏱ Times: <code>M:SS</code> | <code>MM:SS</code> | <code>HH:MM:SS</code>"
    )
    if os.path.exists(video_path):
        with open(video_path, 'rb') as f:
            await context.bot.send_video(
                chat_id=update.effective_chat.id,
                video=f,
                caption=text,
                parse_mode='html'
            )
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
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

import re
import os
import asyncio
import logging
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, MessageHandler, CallbackQueryHandler, filters
from bot.status import ClipStatus
import bot.utils as utils
from bot.auto_clip import generate_clip_list, DetectedClip, generate_youtube_seo
from bot.smart_crop import extract_smart_crop

YOUTUBE_REGEX = re.compile(
    r'(https?://)?(www\.)?(youtube\.com/(watch\?v=|live/)|youtu\.be/|youtube\.com/shorts/)[^\s]+'
)

QUALITY_MAP = {
    "480": "bestvideo[height<=480]+bestaudio/best[height<=480]",
    "720": "bestvideo[height<=720]+bestaudio/best[height<=720]",
    "1080": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
    "4k": "bestvideo[height<=2160]+bestaudio/best",
}


def parse_message(text: str):
    parts = text.strip().split()
    if len(parts) == 1:
        url = parts[0]
        if YOUTUBE_REGEX.match(url):
            return url, None, None
        return None, None, None
    if len(parts) < 3:
        return None, None, None
    url = parts[0]
    start_time = parts[1]
    end_time = parts[2]
    if not YOUTUBE_REGEX.match(url):
        return None, None, None
    return url, start_time, end_time


async def clip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    youtube_link, start_time, end_time = parse_message(text)

    if not youtube_link:
        await update.message.reply_text(
            "Invalid format.\n\n"
            "<b>Manual mode:</b> <code>&lt;URL&gt; &lt;start&gt; &lt;end&gt;</code>\n"
            "<b>Auto mode:</b> <code>&lt;URL&gt;</code>\n\n"
            "Example: <code>https://youtu.be/abc123 1:30 2:45</code>",
            parse_mode='html'
        )
        return

    if start_time is not None and end_time is not None:
        context.user_data['pending_url'] = youtube_link
        context.user_data['pending_start'] = start_time
        context.user_data['pending_end'] = end_time
        context.user_data['mode'] = 'manual'
    else:
        context.user_data['pending_url'] = youtube_link
        context.user_data['mode'] = 'auto'

    keyboard = [
        [
            InlineKeyboardButton("480p", callback_data="quality_480"),
            InlineKeyboardButton("720p", callback_data="quality_720"),
        ],
        [
            InlineKeyboardButton("1080p", callback_data="quality_1080"),
            InlineKeyboardButton("4K", callback_data="quality_4k"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Select video quality:",
        reply_markup=reply_markup
    )


async def quality_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    quality = query.data.replace("quality_", "")
    youtube_link = context.user_data.get('pending_url')
    mode = context.user_data.get('mode', 'auto')

    if not youtube_link:
        await query.edit_message_text("Session expired. Send the link again.")
        return

    quality_format = QUALITY_MAP.get(quality, QUALITY_MAP["720"])

    await query.edit_message_text(f"Downloading in {quality}p...")

    uid = f'{update.callback_query.message.chat_id}-{query.message.message_id}'
    status = ClipStatus(uid, "Downloading", 0, '')
    message = query.message

    os.makedirs('outputs', exist_ok=True)

    try:
        ydl_opts = {'quiet': True, 'no_warnings': True}
        cookies_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cookies.txt')
        if os.path.exists(cookies_path):
            ydl_opts['cookiefile'] = cookies_path
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_link, download=False)
            if 'entries' in info:
                await message.edit_text("Playlists cannot be clipped.")
                return
            status.name = ydl.prepare_filename(info)
            context.user_data['video_title'] = info.get('title', 'YouTube Video')
    except Exception as e:
        logging.error(f"yt-dlp info error: {e}")
        await message.edit_text("Could not fetch video info. Check the URL and try again.")
        return

    try:
        ydl_opts_dl = {
            'format': quality_format,
            'merge_output_format': 'mp4',
            'outtmpl': f'outputs/{uid}.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 3,
        }
        cookies_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cookies.txt')
        if os.path.exists(cookies_path):
            ydl_opts_dl['cookiefile'] = cookies_path
        with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl:
            info = ydl.extract_info(youtube_link, download=True)
            video_path = ydl.prepare_filename(info)
            if not os.path.exists(video_path):
                base = os.path.splitext(video_path)[0]
                for ext in ['.mp4', '.webm', '.mkv']:
                    if os.path.exists(base + ext):
                        video_path = base + ext
                        break
    except Exception as e:
        logging.error(f"yt-dlp download error: {e}")
        await message.edit_text("Failed to download the video. Try again later.")
        return

    if mode == 'manual':
        start_time = context.user_data.get('pending_start')
        end_time = context.user_data.get('pending_end')
        await _handle_manual_mode(update, context, message, uid, status, video_path, start_time, end_time, video_title)
    else:
        await _handle_auto_mode(update, context, message, uid, status, video_path)

    context.user_data.pop('pending_url', None)
    context.user_data.pop('pending_start', None)
    context.user_data.pop('pending_end', None)
    context.user_data.pop('mode', None)


async def _handle_manual_mode(update, context, message, uid, status, video_path, start_time, end_time, video_title='YouTube Video'):
    output_path = f'outputs/{uid}_clipped.mp4'

    def parse_ts(ts):
        parts = ts.split(":")
        parts = [float(p) for p in parts]
        if len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        if len(parts) == 2:
            return parts[0] * 60 + parts[1]
        return parts[0]

    start_sec = parse_ts(start_time)
    end_sec = parse_ts(end_time)

    loop = asyncio.get_event_loop()
    success = await loop.run_in_executor(
        None,
        lambda: extract_smart_crop(
            video_path, start_sec, end_sec,
            output_path, 1080, 1920
        )
    )

    if not success:
        try:
            await message.edit_text("Failed to clip the video. Try different timestamps.")
        except Exception:
            pass
        for p in [video_path, output_path]:
            if os.path.exists(p):
                os.remove(p)
        return

    try:
        await message.edit_text("Uploading...")
    except Exception:
        pass

    try:
        loop = asyncio.get_event_loop()
        seo = await loop.run_in_executor(
            None,
            lambda: generate_youtube_seo(video_title, "Manual clip", start_sec, end_sec)
        )

        caption = (
            f"📝 Title:\n`{seo['title']}`\n\n"
            f"📖 Description:\n`{seo['description']}`\n\n"
            f"🏷 Hashtags:\n`{seo['hashtags']}`"
        )

        with open(output_path, 'rb') as f:
            await update.callback_query.message.reply_video(
                video=f,
                filename=f'{status.name}_clip.mp4',
                caption=caption,
                parse_mode='markdown',
                supports_streaming=True
            )
        await message.delete()
    except Exception as e:
        logging.error(f"Send video error: {e}")
        await message.edit_text("Failed to send the clip. The file might be too large.")
    finally:
        for p in [video_path, output_path]:
            if os.path.exists(p):
                os.remove(p)


async def _handle_auto_mode(update, context, message, uid, status, video_path):
    try:
        await message.edit_text("Extracting audio...")
    except Exception:
        pass

    video_title = context.user_data.get('video_title', 'YouTube Video')

    loop = asyncio.get_event_loop()
    clips = await loop.run_in_executor(
        None, lambda: generate_clip_list(video_path, clip_duration=30.0, top_n=10, video_title=video_title)
    )

    if clips is None:
        await message.edit_text("Failed to analyze audio. Try manual mode with timestamps.")
        if os.path.exists(video_path):
            os.remove(video_path)
        return

    if not clips:
        await message.edit_text(
            "No highlight moments detected. Try manual mode:\n"
            "<code>&lt;URL&gt; &lt;start&gt; &lt;end&gt;</code>",
            parse_mode='html'
        )
        if os.path.exists(video_path):
            os.remove(video_path)
        return

    context.user_data['detected_clips'] = clips
    context.user_data['video_path'] = video_path
    context.user_data['uid'] = uid
    context.user_data['status_name'] = status.name

    clip_list = ""
    for i, c in enumerate(clips):
        mins = int(c.start // 60)
        secs = int(c.start % 60)
        clip_list += f"<b>#{i+1}</b> [{mins}:{secs:02d}] {c.label}\n"

    buttons = []
    row = []
    for i in range(1, len(clips) + 1):
        row.append(InlineKeyboardButton(str(i), callback_data=f"clipcount_{i}"))
        if len(row) == 5:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("ALL", callback_data="clipcount_all")])

    reply_markup = InlineKeyboardMarkup(buttons)

    await message.edit_text(
        f"Found <b>{len(clips)}</b> clips:\n\n"
        f"{clip_list}\n"
        f"How many clips do you want?",
        parse_mode='html',
        reply_markup=reply_markup
    )


async def handle_clip_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("clipcount_"):
        return

    value = data.replace("clipcount_", "")
    clips = context.user_data.get('detected_clips')
    video_path = context.user_data.get('video_path')
    uid = context.user_data.get('uid')
    status_name = context.user_data.get('status_name')
    video_title = context.user_data.get('video_title', 'YouTube Video')

    if not clips or not video_path:
        await query.edit_message_text("Session expired. Send a YouTube link again.")
        return

    if value == "all":
        selected = clips
        count = len(selected)
    else:
        count = int(value)
        if count < 1 or count > len(clips):
            await query.edit_message_text(f"Invalid selection.")
            return
        selected = [clips[count - 1]]
        count = 1
    if value == "all":
        await query.edit_message_text(f"Processing all {len(selected)} clips...")
    else:
        await query.edit_message_text(f"Processing clip {count}...")

    sent_count = 0
    for i, clip_seg in enumerate(selected):
        output_path = f'outputs/{uid}_auto_{i}.mp4'

        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                lambda: extract_smart_crop(
                    video_path, clip_seg.start, clip_seg.end,
                    output_path, 1080, 1920
                )
            )
            if not success:
                logging.error(f"Smart crop failed for clip {i}")
                continue

            seo = await loop.run_in_executor(
                None,
                lambda: generate_youtube_seo(video_title, clip_seg.label, clip_seg.start, clip_seg.end)
            )

            caption = (
                f"📝 Title:\n`{seo['title']}`\n\n"
                f"📖 Description:\n`{seo['description']}`\n\n"
                f"🏷 Hashtags:\n`{seo['hashtags']}`"
            )

            with open(output_path, 'rb') as f:
                await query.message.reply_video(
                    video=f,
                    filename=f'{status_name}_clip_{i+1}.mp4',
                    caption=caption,
                    parse_mode='markdown',
                    supports_streaming=True
                )
                sent_count += 1
        except Exception as e:
            logging.error(f"Failed to process clip {i}: {e}")
        finally:
            if os.path.exists(output_path):
                os.remove(output_path)

    try:
        if sent_count > 0:
            await query.message.reply_text(f"Sent {sent_count}/{count} clips.")
        else:
            await query.message.reply_text("Failed to generate clips. Try manual mode.")
    except Exception:
        pass

    if os.path.exists(video_path):
        os.remove(video_path)

    context.user_data.pop('detected_clips', None)
    context.user_data.pop('video_path', None)
    context.user_data.pop('uid', None)
    context.user_data.pop('status_name', None)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await clip(update, context)

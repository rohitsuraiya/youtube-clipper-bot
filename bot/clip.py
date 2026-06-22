import re
import os
import asyncio
import logging
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from bot.status import ClipStatus
import bot.utils as utils
from bot.auto_clip import generate_clip_list, DetectedClip, generate_youtube_seo
from bot.smart_crop import extract_smart_crop
from bot.jobs import save_job, update_job

YOUTUBE_REGEX = re.compile(
    r'(https?://)?(www\.)?(youtube\.com/(watch\?v=|live/)|youtu\.be/|youtube\.com/shorts/)[^\s]+'
)

QUALITY_FORMAT_MAP = {
    "480p": "best[height<=480]/best",
    "720p": "best[height<=720]/best",
    "1080p": "best[height<=1080]/best",
    "4K": "best",
}


def _get_ydl_opts(cookies_path):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'no_check_certificates': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        },
    }
    if cookies_path and os.path.exists(cookies_path):
        opts['cookiefile'] = cookies_path
    return opts


async def start_processing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    draft = context.user_data

    url = draft.get('url')
    if not url:
        await query.edit_message_text("❌ No video URL found. Please start over.",
                                      reply_markup=InlineKeyboardMarkup([
                                          [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
                                      ]))
        return

    user_id = update.effective_user.id
    job_id = save_job(
        user_id, url,
        style=draft.get('style', ''),
        quality=draft.get('quality', ''),
        length=draft.get('length', ''),
        face_tracking=draft.get('face_tracking', False),
        format=draft.get('format', ''),
        status='processing'
    )

    message = query.message
    cookies_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cookies.txt')
    quality = draft.get('quality', '720p')
    quality_format = QUALITY_FORMAT_MAP.get(quality, QUALITY_FORMAT_MAP['720p'])

    try:
        await message.edit_text(
            f"⚙️ <b>Processing Job #{job_id}</b>\n\n"
            "📥 Downloading video...\n"
            "📜 Extracting transcript...\n"
            "🧠 AI analyzing content...\n"
            "🎯 Finding viral moments...",
            parse_mode='html'
        )
    except Exception:
        try:
            await message.delete()
        except Exception:
            pass
        message = await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"⚙️ <b>Processing Job #{job_id}</b>\n\n"
                 "📥 Downloading video...\n"
                 "📜 Extracting transcript...\n"
                 "🧠 AI analyzing content...\n"
                 "🎯 Finding viral moments...",
            parse_mode='html'
        )

    uid = f'{update.callback_query.message.chat_id}-{query.message.message_id}'
    status = ClipStatus(uid, "Downloading", 0, '')

    os.makedirs('outputs', exist_ok=True)

    try:
        ydl_opts = _get_ydl_opts(cookies_path)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                await message.edit_text("❌ Playlists cannot be clipped.",
                                        reply_markup=InlineKeyboardMarkup([
                                            [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
                                        ]))
                update_job(job_id, status='failed')
                return
            status.name = ydl.prepare_filename(info)
            context.user_data['video_title'] = info.get('title', 'YouTube Video')
    except Exception as e:
        logging.error(f"yt-dlp info error (attempt 1): {e}")
        await asyncio.sleep(3)
        try:
            ydl_opts = _get_ydl_opts(cookies_path)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if 'entries' in info:
                    await message.edit_text("❌ Playlists cannot be clipped.",
                                            reply_markup=InlineKeyboardMarkup([
                                                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
                                            ]))
                    update_job(job_id, status='failed')
                    return
                status.name = ydl.prepare_filename(info)
                context.user_data['video_title'] = info.get('title', 'YouTube Video')
        except Exception as e2:
            logging.error(f"yt-dlp info error (attempt 2): {e2}")
            await message.edit_text("❌ Could not fetch video info. Check the URL and try again.",
                                    reply_markup=InlineKeyboardMarkup([
                                        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
                                    ]))
            update_job(job_id, status='failed')
            return

    try:
        ydl_opts_dl = _get_ydl_opts(cookies_path)
        ydl_opts_dl.update({
            'format': quality_format,
            'merge_output_format': 'mp4',
            'outtmpl': f'outputs/{uid}.%(ext)s',
            'socket_timeout': 30,
            'retries': 3,
        })
        with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl:
            info = ydl.extract_info(url, download=True)
            video_path = ydl.prepare_filename(info)
            if not os.path.exists(video_path):
                base = os.path.splitext(video_path)[0]
                for ext in ['.mp4', '.webm', '.mkv']:
                    if os.path.exists(base + ext):
                        video_path = base + ext
                        break
    except Exception as e:
        logging.error(f"yt-dlp download error (attempt 1): {e}")
        logging.info("Retrying download with best format...")
        try:
            ydl_opts_dl = _get_ydl_opts(cookies_path)
            ydl_opts_dl.update({
                'format': 'best',
                'merge_output_format': 'mp4',
                'outtmpl': f'outputs/{uid}.%(ext)s',
                'socket_timeout': 30,
                'retries': 3,
            })
            with yt_dlp.YoutubeDL(ydl_opts_dl) as ydl:
                info = ydl.extract_info(url, download=True)
                video_path = ydl.prepare_filename(info)
                if not os.path.exists(video_path):
                    base = os.path.splitext(video_path)[0]
                    for ext in ['.mp4', '.webm', '.mkv']:
                        if os.path.exists(base + ext):
                            video_path = base + ext
                            break
        except Exception as e2:
            logging.error(f"yt-dlp download error (attempt 2): {e2}")
            await message.edit_text("❌ Failed to download the video. Try again later.",
                                    reply_markup=InlineKeyboardMarkup([
                                        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
                                    ]))
            update_job(job_id, status='failed')
            return

    video_title = context.user_data.get('video_title', 'YouTube Video')
    face_tracking = draft.get('face_tracking', False)

    try:
        await message.edit_text(
            f"⚙️ <b>Processing Job #{job_id}</b>\n\n"
            "🧠 AI analyzing content...\n"
            "🎯 Finding viral moments...",
            parse_mode='html'
        )
    except Exception:
        pass

    loop = asyncio.get_event_loop()
    clips = await loop.run_in_executor(
        None, lambda: generate_clip_list(video_path, clip_duration=30.0, top_n=10, video_title=video_title)
    )

    if clips is None:
        await message.edit_text("❌ Failed to analyze audio. Try manual mode with timestamps.",
                                reply_markup=InlineKeyboardMarkup([
                                    [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
                                ]))
        update_job(job_id, status='failed')
        if os.path.exists(video_path):
            os.remove(video_path)
        return

    if not clips:
        await message.edit_text(
            "❌ No highlight moments detected.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
            ])
        )
        update_job(job_id, status='failed')
        if os.path.exists(video_path):
            os.remove(video_path)
        return

    context.user_data['detected_clips'] = clips
    context.user_data['video_path'] = video_path
    context.user_data['uid'] = uid
    context.user_data['status_name'] = status.name
    context.user_data['job_id'] = job_id

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
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(buttons)

    await message.edit_text(
        f"✅ <b>Analysis Complete!</b>\n\n"
        f"Found <b>{len(clips)}</b> clips:\n\n"
        f"{clip_list}\n"
        f"Select which clips to send:",
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
    job_id = context.user_data.get('job_id')
    face_tracking = context.user_data.get('face_tracking', False)

    if not clips or not video_path:
        await query.edit_message_text("Session expired. Send a YouTube link again.")
        return

    if value == "all":
        selected = clips
    else:
        count = int(value)
        if count < 1 or count > len(clips):
            await query.edit_message_text("Invalid selection.")
            return
        selected = [clips[count - 1]]

    if value == "all":
        await query.edit_message_text(f"Processing all {len(selected)} clips...")
    else:
        await query.edit_message_text(f"Processing clip {value}...")

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
            await query.message.reply_text(
                f"✅ Sent {sent_count} clips!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📹 Create More Clips", callback_data="create_clips")],
                    [InlineKeyboardButton("📂 My Jobs", callback_data="my_jobs"),
                     InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
                ])
            )
        else:
            await query.message.reply_text(
                "❌ Failed to generate clips. Try manual mode.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]
                ])
            )
    except Exception:
        pass

    if job_id:
        update_job(job_id, status='completed', clips_generated=sent_count)

    if os.path.exists(video_path):
        os.remove(video_path)

    for key in ['detected_clips', 'video_path', 'uid', 'status_name', 'job_id', 'url',
                'style', 'quality', 'length', 'face_tracking', 'format', 'step', 'video_title']:
        context.user_data.pop(key, None)

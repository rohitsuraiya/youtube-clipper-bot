import subprocess
import os
import logging
import json
from typing import Optional


def extract_smart_crop(video_path: str, start: float, end: float, output_path: str,
                       target_w: int = 1080, target_h: int = 1920) -> bool:

    duration = end - start

    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        video_path
    ]
    try:
        proc = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
        info = json.loads(proc.stdout)
        vw = int(info["streams"][0]["width"])
        vh = int(info["streams"][0]["height"])
    except Exception:
        vw, vh = 1920, 1080

    target_ratio = target_w / target_h
    video_ratio = vw / vh

    if video_ratio < target_ratio:
        crop_w = vw
        crop_h = int(vw / target_ratio)
    else:
        crop_h = vh
        crop_w = int(vh * target_ratio)

    crop_w = min(crop_w, vw)
    crop_h = min(crop_h, vh)

    x_offset = (vw - crop_w) // 2
    y_offset = (vh - crop_h) // 2

    filter_complex = (
        f"crop={crop_w}:{crop_h}:{x_offset}:{y_offset},"
        f"scale={target_w}:{target_h}:flags=lanczos"
    )

    try:
        proc = subprocess.run([
            'ffmpeg', '-y',
            '-ss', str(start),
            '-i', video_path,
            '-t', str(duration),
            '-vf', filter_complex,
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-crf', '28',
            '-c:a', 'aac',
            '-b:a', '96k',
            '-movflags', '+faststart',
            output_path
        ], capture_output=True, text=True, timeout=120)

        if proc.returncode != 0:
            logging.error(f"ffmpeg crop failed: {proc.stderr[:300]}")
            return False

        return os.path.exists(output_path) and os.path.getsize(output_path) > 0

    except Exception as e:
        logging.error(f"ffmpeg crop error: {e}")
        return False

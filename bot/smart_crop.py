import subprocess
import os
import logging
import json
import cv2
import numpy as np
from typing import List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class FaceRegion:
    x: int
    y: int
    w: int
    h: int
    cx: int
    cy: int


FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')


def _get_video_info(video_path: str) -> Tuple[int, int, float]:
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

    dur_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ]
    try:
        proc = subprocess.run(dur_cmd, capture_output=True, text=True, timeout=10)
        duration = float(proc.stdout.strip())
    except Exception:
        duration = 0

    return vw, vh, duration


def _extract_frame(video_path: str, timestamp: float) -> Optional[np.ndarray]:
    try:
        cmd = [
            "ffmpeg", "-ss", str(timestamp), "-i", video_path,
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-v", "error", "-"
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=15)
        if proc.returncode != 0:
            return None

        vw, vh, _ = _get_video_info(video_path)
        frame = np.frombuffer(proc.stdout, dtype=np.uint8).reshape((vh, vw, 3))
        return frame
    except Exception as e:
        logging.warning(f"Frame extraction failed: {e}")
        return None


def detect_faces(frame: np.ndarray) -> List[FaceRegion]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    faces_raw = FACE_CASCADE.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(int(w * 0.05), int(h * 0.05))
    )

    if len(faces_raw) == 0:
        return []

    faces = []
    for (x, y, fw, fh) in faces_raw:
        faces.append(FaceRegion(
            x=int(x), y=int(y), w=int(fw), h=int(fh),
            cx=int(x + fw // 2), cy=int(y + fh // 2),
        ))

    faces.sort(key=lambda f: f.cx)
    return faces


def _detect_faces_multi_frame(video_path: str, start: float, end: float) -> List[FaceRegion]:
    mid = (start + end) / 2
    samples = [start + 0.5, mid, end - 0.5]
    samples = [t for t in samples if start <= t <= end]

    all_faces = []
    for t in samples:
        frame = _extract_frame(video_path, t)
        if frame is not None:
            faces = detect_faces(frame)
            all_faces.extend(faces)

    if not all_faces:
        return []

    merged = []
    used = set()
    for i, f1 in enumerate(all_faces):
        if i in used:
            continue
        group = [f1]
        used.add(i)
        for j, f2 in enumerate(all_faces):
            if j in used:
                continue
            if abs(f1.cx - f2.cx) < 100 and abs(f1.cy - f2.cy) < 100:
                group.append(f2)
                used.add(j)
        avg_cx = int(np.mean([f.cx for f in group]))
        avg_cy = int(np.mean([f.cy for f in group]))
        avg_w = int(np.mean([f.w for f in group]))
        avg_h = int(np.mean([f.h for f in group]))
        merged.append(FaceRegion(
            x=avg_cx - avg_w // 2, y=avg_cy - avg_h // 2,
            w=avg_w, h=avg_h, cx=avg_cx, cy=avg_cy,
        ))

    return merged


def _layout_single_face(face: FaceRegion, vw: int, vh: int, target_w: int, target_h: int) -> Tuple[int, int, int, int]:
    target_ratio = target_w / target_h

    crop_h = min(vh, int(vw / target_ratio))
    crop_w = min(vw, int(crop_h * target_ratio))

    cx = face.cx
    cy = face.cy

    x = max(0, min(cx - crop_w // 2, vw - crop_w))
    y = max(0, min(cy - crop_h // 2, vh - crop_h))

    return crop_w, crop_h, x, y


def _layout_two_faces(faces: List[FaceRegion], vw: int, vh: int, target_w: int, target_h: int) -> Tuple[int, int, int, int]:
    target_ratio = target_w / target_h

    top_face = min(faces, key=lambda f: f.cy)
    bot_face = max(faces, key=lambda f: f.cy)

    top_y = max(0, top_face.cy - top_face.h)
    bot_y = min(vh, bot_face.cy + bot_face.h)

    region_h = bot_y - top_y
    if region_h < vh * 0.5:
        padding = int((vh * 0.6 - region_h) / 2)
        top_y = max(0, top_y - padding)
        bot_y = min(vh, bot_y + padding)
        region_h = bot_y - top_y

    crop_h = min(vh, int(vw / target_ratio))
    crop_w = min(vw, int(crop_h * target_ratio))

    center_y = (top_y + bot_y) // 2
    y = max(0, min(center_y - crop_h // 2, vh - crop_h))

    avg_cx = (top_face.cx + bot_face.cx) // 2
    x = max(0, min(avg_cx - crop_w // 2, vw - crop_w))

    return crop_w, crop_h, x, y


def _layout_multi_faces(faces: List[FaceRegion], vw: int, vh: int, target_w: int, target_h: int) -> Tuple[int, int, int, int]:
    target_ratio = target_w / target_h

    min_x = min(f.x for f in faces)
    max_x = max(f.x + f.w for f in faces)
    min_y = min(f.y for f in faces)
    max_y = max(f.y + f.h for f in faces)

    margin_x = int(vw * 0.1)
    margin_y = int(vh * 0.1)

    region_x = max(0, min_x - margin_x)
    region_y = max(0, min_y - margin_y)
    region_w = min(vw, max_x + margin_x) - region_x
    region_h = min(vh, max_y + margin_y) - region_y

    region_ratio = region_w / region_h

    if region_ratio < target_ratio:
        crop_w = region_w
        crop_h = int(crop_w / target_ratio)
    else:
        crop_h = region_h
        crop_w = int(crop_h * target_ratio)

    crop_w = min(crop_w, vw)
    crop_h = min(crop_h, vh)

    center_x = region_x + region_w // 2
    center_y = region_y + region_h // 2

    x = max(0, min(center_x - crop_w // 2, vw - crop_w))
    y = max(0, min(center_y - crop_h // 2, vh - crop_h))

    return crop_w, crop_h, x, y


def _layout_center(vw: int, vh: int, target_w: int, target_h: int) -> Tuple[int, int, int, int]:
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

    x = (vw - crop_w) // 2
    y = (vh - crop_h) // 2

    return crop_w, crop_h, x, y


def extract_smart_crop(video_path: str, start: float, end: float, output_path: str,
                       target_w: int = 1080, target_h: int = 1920) -> bool:

    duration = end - start
    vw, vh, total_dur = _get_video_info(video_path)

    frame_start = min(start + 0.5, end - 0.5) if duration > 1 else start
    frame = _extract_frame(video_path, frame_start)

    if frame is not None:
        faces = detect_faces(frame)
        if not faces and duration > 2:
            faces = _detect_faces_multi_frame(video_path, start, end)
    else:
        faces = []

    num_faces = len(faces)
    logging.info(f"Smart crop: detected {num_faces} face(s)")

    if num_faces == 0:
        crop_w, crop_h, x, y = _layout_center(vw, vh, target_w, target_h)
        layout = "center"
    elif num_faces == 1:
        crop_w, crop_h, x, y = _layout_single_face(faces[0], vw, vh, target_w, target_h)
        layout = "single-face"
    elif num_faces == 2:
        crop_w, crop_h, x, y = _layout_two_faces(faces, vw, vh, target_w, target_h)
        layout = "two-face"
    else:
        crop_w, crop_h, x, y = _layout_multi_faces(faces, vw, vh, target_w, target_h)
        layout = f"multi-face({num_faces})"

    logging.info(f"Layout: {layout}, crop={crop_w}x{crop_h} at ({x},{y})")

    filter_complex = (
        f"crop={crop_w}:{crop_h}:{x}:{y},"
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

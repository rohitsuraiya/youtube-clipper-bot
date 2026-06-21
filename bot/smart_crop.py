import subprocess
import os
import logging
import json
import cv2
import numpy as np
from typing import List, Tuple, Optional


PROTOTXT = os.path.join(os.path.dirname(__file__), '..', 'models', 'deploy.prototxt')
CAFFEMODEL = os.path.join(os.path.dirname(__file__), '..', 'models', 'res10_300x300_ssd_iter_140000.caffemodel')
face_net = None

def _init_face_net():
    global face_net
    if face_net is not None:
        return face_net
    if os.path.exists(PROTOTXT) and os.path.exists(CAFFEMODEL):
        face_net = cv2.dnn.readNetFromCaffe(PROTOTXT, CAFFEMODEL)
    return face_net


def _get_video_info(video_path: str) -> Tuple[int, int]:
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
        return int(info["streams"][0]["width"]), int(info["streams"][0]["height"])
    except Exception:
        return 1920, 1080


def _extract_frame(video_path: str, timestamp: float) -> Optional[np.ndarray]:
    vw, vh = _get_video_info(video_path)
    try:
        cmd = [
            "ffmpeg", "-ss", str(timestamp), "-i", video_path,
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-v", "error", "-"
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=15)
        if proc.returncode != 0:
            return None
        frame = np.frombuffer(proc.stdout, dtype=np.uint8).reshape((vh, vw, 3))
        return frame
    except Exception:
        return None


def detect_faces(frame: np.ndarray) -> List[Tuple[int, int, int, int]]:
    net = _init_face_net()
    h, w, _ = frame.shape

    if net is not None:
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
        net.setInput(blob)
        detections = net.forward()

        faces = []
        for i in range(detections.shape[2]):
            conf = detections[0, 0, i, 2]
            if conf > 0.5:
                box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                x1, y1, x2, y2 = box.astype(int)
                pad_w = int((x2 - x1) * 0.6)
                pad_h = int((y2 - y1) * 0.8)
                x1 = max(0, x1 - pad_w)
                y1 = max(0, y1 - pad_h)
                x2 = min(w, x2 + pad_w)
                y2 = min(h, y2 + pad_h)
                faces.append((x1, y1, x2, y2))
        return sorted(faces, key=lambda f: f[0])

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    min_dim = int(min(h, w) * 0.05)
    rects = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml') \
        .detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_dim, min_dim))

    faces = []
    for (x, y, fw, fh) in rects:
        pad_w = int(fw * 0.6)
        pad_h = int(fh * 0.8)
        x1 = max(0, x - pad_w)
        y1 = max(0, y - pad_h)
        x2 = min(w, x + fw + pad_w)
        y2 = min(h, y + fh + pad_h)
        faces.append((x1, y1, x2, y2))
    return sorted(faces, key=lambda f: f[0])


def _detect_faces_multi_frame(video_path: str, start: float, end: float) -> List[Tuple[int, int, int, int]]:
    mid = (start + end) / 2
    samples = [t for t in [start + 0.5, mid, end - 0.5] if start <= t <= end]

    all_faces = []
    for t in samples:
        frame = _extract_frame(video_path, t)
        if frame is not None:
            all_faces.extend(detect_faces(frame))

    if not all_faces:
        return []

    merged = []
    used = set()
    for i, f1 in enumerate(all_faces):
        if i in used:
            continue
        group = [f1]
        used.add(i)
        cx1 = (f1[0] + f1[2]) // 2
        cy1 = (f1[1] + f1[3]) // 2
        for j, f2 in enumerate(all_faces):
            if j in used:
                continue
            cx2 = (f2[0] + f2[2]) // 2
            cy2 = (f2[1] + f2[3]) // 2
            if abs(cx1 - cx2) < 100 and abs(cy1 - cy2) < 100:
                group.append(f2)
                used.add(j)
        avg = lambda idx: int(np.mean([f[idx] for f in group]))
        merged.append((avg(0), avg(1), avg(2), avg(3)))

    return merged


def extract_smart_crop(video_path: str, start: float, end: float, output_path: str,
                       target_w: int = 1080, target_h: int = 1920) -> bool:

    duration = end - start
    vw, vh = _get_video_info(video_path)

    frame_t = min(start + 0.5, end - 0.5) if duration > 1 else start
    frame = _extract_frame(video_path, frame_t)

    if frame is not None:
        faces = detect_faces(frame)
        if not faces and duration > 2:
            faces = _detect_faces_multi_frame(video_path, start, end)
    else:
        faces = []

    num_faces = len(faces)
    logging.info(f"Smart crop: {num_faces} face(s) detected")

    if num_faces >= 2:
        f1, f2 = faces[0], faces[1]
        half_h = target_h // 2
        filter_complex = (
            f"[0:v]crop=iw:ih/2:0:0,scale={target_w}:{half_h}:flags=lanczos[top];"
            f"[0:v]crop=iw:ih/2:0:ih/2,scale={target_w}:{half_h}:flags=lanczos[bot];"
            f"[top][bot]vstack=inputs=2[out]"
        )
        layout = "split-screen"

    elif num_faces == 1:
        x1, y1, x2, y2 = faces[0]
        target_ratio = target_w / target_h
        fw = x2 - x1
        crop_h = min(vh, int(fw / target_ratio))
        crop_w = min(vw, int(crop_h * target_ratio))
        cx = max(crop_w // 2, min((x1 + x2) // 2, vw - crop_w // 2))
        cy = max(crop_h // 2, min((y1 + y2) // 2, vh - crop_h // 2))
        crop_x = int(cx - crop_w // 2)
        crop_y = int(cy - crop_h // 2)
        filter_complex = (
            f"crop={int(crop_w)}:{int(crop_h)}:{crop_x}:{crop_y},"
            f"scale={target_w}:{target_h}:flags=lanczos"
        )
        layout = "single-face"

    else:
        target_ratio = target_w / target_h
        video_ratio = vw / vh
        if video_ratio < target_ratio:
            crop_w, crop_h = vw, int(vw / target_ratio)
        else:
            crop_h, crop_w = vh, int(vh * target_ratio)
        crop_w, crop_h = min(crop_w, vw), min(crop_h, vh)
        crop_x, crop_y = (vw - crop_w) // 2, (vh - crop_h) // 2
        filter_complex = (
            f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
            f"scale={target_w}:{target_h}:flags=lanczos"
        )
        layout = "center"

    logging.info(f"Layout: {layout}")

    try:
        cmd = ['ffmpeg', '-y', '-ss', str(start), '-i', video_path, '-t', str(duration)]

        if num_faces >= 2:
            cmd += ['-filter_complex', filter_complex, '-map', '[out]']
        else:
            cmd += ['-vf', filter_complex]

        cmd += [
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
            '-c:a', 'aac', '-b:a', '96k',
            '-movflags', '+faststart', output_path
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if proc.returncode != 0:
            logging.error(f"ffmpeg crop failed: {proc.stderr[:300]}")
            return False

        return os.path.exists(output_path) and os.path.getsize(output_path) > 0

    except Exception as e:
        logging.error(f"ffmpeg crop error: {e}")
        return False

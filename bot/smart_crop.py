import subprocess
import os
import logging
import json
import cv2
import numpy as np
from typing import List, Tuple, Optional, Dict
from dataclasses import dataclass


PROTOTXT = os.path.join(os.path.dirname(__file__), '..', 'models', 'deploy.prototxt')
CAFFEMODEL = os.path.join(os.path.dirname(__file__), '..', 'models', 'res10_300x300_ssd_iter_140000.caffemodel')
face_net = None


@dataclass
class TrackedFace:
    x: float
    y: float
    w: float
    h: float
    confidence: float


@dataclass
class CropTarget:
    crop_x: float
    crop_y: float
    crop_w: float
    crop_h: float


def _init_face_net():
    global face_net
    if face_net is not None:
        return face_net
    if os.path.exists(PROTOTXT) and os.path.exists(CAFFEMODEL):
        face_net = cv2.dnn.readNetFromCaffe(PROTOTXT, CAFFEMODEL)
    return face_net


def _get_video_info(video_path: str) -> Tuple[int, int, float]:
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate",
        "-of", "json",
        video_path
    ]
    try:
        proc = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=10)
        info = json.loads(proc.stdout)
        stream = info["streams"][0]
        w, h = int(stream["width"]), int(stream["height"])
        fps_str = stream.get("r_frame_rate", "30/1")
        num, den = map(int, fps_str.split("/"))
        fps = num / den if den else 30.0
        return w, h, fps
    except Exception:
        return 1920, 1080, 30.0


def _extract_frame(video_path: str, timestamp: float, vw: int, vh: int) -> Optional[np.ndarray]:
    try:
        cmd = [
            "ffmpeg", "-ss", str(timestamp), "-i", video_path,
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-v", "error", "-"
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=15)
        if proc.returncode != 0 or len(proc.stdout) == 0:
            return None
        expected = vw * vh * 3
        if len(proc.stdout) < expected:
            return None
        frame = np.frombuffer(proc.stdout[:expected], dtype=np.uint8).reshape((vh, vw, 3))
        return frame
    except Exception:
        return None


def detect_faces(frame: np.ndarray, min_confidence: float = 0.5) -> List[TrackedFace]:
    net = _init_face_net()
    h, w, _ = frame.shape

    if net is not None:
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 1.0, (300, 300), (104.0, 177.0, 123.0))
        net.setInput(blob)
        detections = net.forward()

        faces = []
        for i in range(detections.shape[2]):
            conf = detections[0, 0, i, 2]
            if conf > min_confidence:
                box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
                x1, y1, x2, y2 = box.astype(float)
                pad_w = (x2 - x1) * 0.3
                pad_h = (y2 - y1) * 0.4
                x1 = max(0, x1 - pad_w)
                y1 = max(0, y1 - pad_h)
                x2 = min(w, x2 + pad_w)
                y2 = min(h, y2 + pad_h)
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                fw, fh = x2 - x1, y2 - y1
                faces.append(TrackedFace(cx - fw/2, cy - fh/2, fw, fh, conf))
        return sorted(faces, key=lambda f: f.x)

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    min_dim = int(min(h, w) * 0.05)
    rects = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml') \
        .detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_dim, min_dim))

    faces = []
    for (x, y, fw, fh) in rects:
        pad_w = fw * 0.3
        pad_h = fh * 0.4
        x1 = max(0, x - pad_w)
        y1 = max(0, y - pad_h)
        x2 = min(w, x + fw + pad_w)
        y2 = min(h, y + fh + pad_h)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        nfw, nfh = x2 - x1, y2 - y1
        faces.append(TrackedFace(cx - nfw/2, cy - nfh/2, nfw, nfh, 0.5))
    return sorted(faces, key=lambda f: f.x)


def _track_faces_across_clip(video_path: str, start: float, end: float,
                              vw: int, vh: int, sample_interval: float = 0.5) -> List[List[TrackedFace]]:
    duration = end - start
    times = []
    t = start + 0.25
    while t < end - 0.25:
        times.append(t)
        t += sample_interval

    if not times:
        times = [(start + end) / 2]

    all_faces = []
    for t in times:
        frame = _extract_frame(video_path, t, vw, vh)
        if frame is not None:
            faces = detect_faces(frame)
            all_faces.append((t, faces))
        else:
            all_faces.append((t, []))

    return all_faces


def _smooth_positions(positions: List[Optional[Tuple[float, float]]],
                      smoothing: float = 0.4) -> List[Tuple[float, float]]:
    if not positions:
        return []

    result = []
    last_valid = None

    for pos in positions:
        if pos is not None:
            if last_valid is None:
                last_valid = pos
            else:
                last_valid = (
                    last_valid[0] * (1 - smoothing) + pos[0] * smoothing,
                    last_valid[1] * (1 - smoothing) + pos[1] * smoothing,
                )
        result.append(last_valid)

    first_valid = next((p for p in result if p is not None), None)
    if first_valid:
        for i in range(len(result)):
            if result[i] is None:
                result[i] = first_valid
            else:
                break

    last_valid = None
    for i in range(len(result) - 1, -1, -1):
        if result[i] is not None:
            last_valid = result[i]
        elif last_valid:
            result[i] = last_valid

    return [(p[0], p[1]) if p is not None else None for p in result]


def _compute_crop_for_face(face_center_x: float, face_center_y: float,
                           face_w: float, face_h: float,
                           vw: int, vh: int,
                           target_w: int, target_h: int,
                           face_scale: float = 1.8) -> CropTarget:
    target_ratio = target_w / target_h

    crop_h = max(face_h * face_scale, vh * 0.4)
    crop_h = min(crop_h, vh)
    crop_w = crop_h * target_ratio

    if crop_w > vw:
        crop_w = vw
        crop_h = crop_w / target_ratio

    cx = max(crop_w / 2, min(face_center_x, vw - crop_w / 2))

    eye_y = face_center_y - face_h * 0.15
    desired_top = eye_y - crop_h * 0.3
    cy = max(crop_h / 2, min(desired_top + crop_h / 2, vh - crop_h / 2))

    crop_x = cx - crop_w / 2
    crop_y = cy - crop_h / 2

    crop_x = max(0, min(crop_x, vw - crop_w))
    crop_y = max(0, min(crop_y, vh - crop_h))

    return CropTarget(crop_x, crop_y, crop_w, crop_h)


def _select_largest_face(faces: List[TrackedFace]) -> Optional[TrackedFace]:
    if not faces:
        return None
    return max(faces, key=lambda f: f.w * f.h)


def extract_smart_crop(video_path: str, start: float, end: float, output_path: str,
                       target_w: int = 1080, target_h: int = 1920,
                       auto_reframe: bool = True) -> bool:
    duration = end - start
    vw, vh, fps = _get_video_info(video_path)

    tracked = _track_faces_across_clip(video_path, start, end, vw, vh, sample_interval=0.5)

    face_positions = []
    face_sizes = []
    for t, faces in tracked:
        if faces:
            largest = _select_largest_face(faces)
            if largest:
                face_positions.append((largest.x + largest.w / 2, largest.y + largest.h / 2))
                face_sizes.append((largest.w, largest.h))
                continue
        face_positions.append(None)
        face_sizes.append(None)

    valid_sizes = [(w, h) for w, h in face_sizes if w is not None]
    if valid_sizes:
        avg_fw = np.mean([s[0] for s in valid_sizes])
        avg_fh = np.mean([s[1] for s in valid_sizes])
    else:
        avg_fw, avg_fh = vw * 0.3, vh * 0.3

    smoothed = _smooth_positions(face_positions, smoothing=0.35)
    has_faces = any(p is not None for p in face_positions)

    logging.info(f"Smart crop: tracked {len([p for p in face_positions if p])}/{len(face_positions)} frames with faces")

    if has_faces and len(smoothed) > 0:
        valid_smoothed = [p for p in smoothed if p is not None]
        if not valid_smoothed:
            valid_smoothed = [(vw / 2, vh / 2)]
        center_x, center_y = valid_smoothed[len(valid_smoothed) // 2]
        crop = _compute_crop_for_face(
            center_x, center_y, avg_fw, avg_fh,
            vw, vh, target_w, target_h,
            face_scale=2.0
        )

        crop_x = int(max(0, min(crop.crop_x, vw - crop.crop_w)))
        crop_y = int(max(0, min(crop.crop_y, vh - crop.crop_h)))
        crop_w = int(min(crop.crop_w, vw - crop_x))
        crop_h = int(min(crop.crop_h, vh - crop_y))

        if auto_reframe and len(smoothed) > 2:
            segments = []
            n = len(smoothed)
            seg_size = max(1, n // 8)
            for i in range(0, n, seg_size):
                seg_positions = [p for p in smoothed[i:i+seg_size] if p is not None]
                if seg_positions:
                    avg_x = np.mean([p[0] for p in seg_positions])
                    avg_y = np.mean([p[1] for p in seg_positions])
                else:
                    avg_x, avg_y = center_x, center_y

                seg_crop = _compute_crop_for_face(
                    avg_x, avg_y, avg_fw, avg_fh,
                    vw, vh, target_w, target_h,
                    face_scale=2.0
                )
                segments.append(seg_crop)

            if len(segments) > 1:
                sx = int(max(0, min(segments[0].crop_x, vw - segments[0].crop_w)))
                sy = int(max(0, min(segments[0].crop_y, vh - segments[0].crop_h)))
                sw = int(min(segments[0].crop_w, vw - sx))
                sh = int(min(segments[0].crop_h, vh - sy))

                enable_tb = f"enable='between(t,{start},{start + duration / len(segments)})'"
                filters = [f"crop={sw}:{sh}:{sx}:{sy}"]

                for i, seg in enumerate(segments[1:], 1):
                    seg_start_t = start + (duration * i / len(segments))
                    seg_end_t = start + (duration * (i + 1) / len(segments))
                    nsx = int(max(0, min(seg.crop_x, vw - seg.crop_w)))
                    nsy = int(max(0, min(seg.crop_y, vh - seg.crop_h)))
                    nsw = int(min(seg.crop_w, vw - nsx))
                    nsh = int(min(seg.crop_h, vh - nsy))
                    if nsw > 0 and nsh > 0:
                        filters.append(
                            f"crop={nsw}:{nsh}:{nsx}:{nsy}:enable='between(t,{seg_start_t},{seg_end_t})'"
                        )

                vf = ",".join(filters) + f",scale={target_w}:{target_h}:flags=lanczos"
            else:
                vf = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}:flags=lanczos"
        else:
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}:flags=lanczos"

        layout = "face-track"

    else:
        target_ratio = target_w / target_h
        video_ratio = vw / vh
        if video_ratio < target_ratio:
            crop_w, crop_h = vw, int(vw / target_ratio)
        else:
            crop_h, crop_w = vh, int(vh * target_ratio)
        crop_w, crop_h = min(crop_w, vw), min(crop_h, vh)
        crop_x, crop_y = (vw - crop_w) // 2, (vh - crop_h) // 2
        vf = f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={target_w}:{target_h}:flags=lanczos"
        layout = "center"

    logging.info(f"Layout: {layout}")

    try:
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start),
            '-i', video_path,
            '-t', str(duration),
            '-vf', vf,
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            '-pix_fmt', 'yuv420p',
            output_path
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if proc.returncode != 0:
            logging.error(f"ffmpeg crop failed: {proc.stderr[:500]}")
            return False

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            logging.error("ffmpeg produced empty output")
            return False

        return True

    except Exception as e:
        logging.error(f"ffmpeg crop error: {e}")
        return False


def extract_split_screen(video_path: str, start: float, end: float, output_path: str,
                         target_w: int = 1080, target_h: int = 1920) -> bool:
    duration = end - start
    vw, vh, fps = _get_video_info(video_path)

    tracked = _track_faces_across_clip(video_path, start, end, vw, vh, sample_interval=0.5)

    face_data = []
    for t, faces in tracked:
        if faces:
            sorted_faces = sorted(faces, key=lambda f: f.x)
            face_data.append(sorted_faces)

    if not face_data:
        logging.info("No faces for split-screen, using center crop")
        return extract_smart_crop(video_path, start, end, output_path, target_w, target_h, auto_reframe=False)

    representative = face_data[len(face_data) // 2]
    if len(representative) < 2:
        representative = face_data[0]

    f1, f2 = representative[0], representative[1]
    c1x = f1.x + f1.w / 2
    c1y = f1.y + f1.h / 2
    c2x = f2.x + f2.w / 2
    c2y = f2.y + f2.h / 2

    half_h = target_h // 2
    half_ratio = target_w / half_h
    face_scale = 2.2

    fw1 = max(f1.w * face_scale, vw * 0.3)
    fh1 = fw1 / half_ratio
    fh1 = max(fh1, vh * 0.3)
    fw1 = fh1 * half_ratio

    crop1_x = max(0, min(int(c1x - fw1 / 2), vw - int(fw1)))
    crop1_y = max(0, min(int(c1y - fh1 / 2), vh - int(fh1)))
    crop1_w = int(min(fw1, vw - crop1_x))
    crop1_h = int(min(fh1, vh - crop1_y))

    fw2 = max(f2.w * face_scale, vw * 0.3)
    fh2 = fw2 / half_ratio
    fh2 = max(fh2, vh * 0.3)
    fw2 = fh2 * half_ratio

    crop2_x = max(0, min(int(c2x - fw2 / 2), vw - int(fw2)))
    crop2_y = max(0, min(int(c2y - fh2 / 2), vh - int(fh2)))
    crop2_w = int(min(fw2, vw - crop2_x))
    crop2_h = int(min(fh2, vh - crop2_y))

    filter_complex = (
        f"[0:v]crop={crop1_w}:{crop1_h}:{crop1_x}:{crop1_y},"
        f"scale={target_w}:{half_h}:flags=lanczos[top];"
        f"[0:v]crop={crop2_w}:{crop2_h}:{crop2_x}:{crop2_y},"
        f"scale={target_w}:{half_h}:flags=lanczos[bot];"
        f"[top][bot]vstack=inputs=2[out]"
    )

    logging.info(f"Split-screen: face1 at ({crop1_x},{crop1_y}) face2 at ({crop2_x},{crop2_y})")

    try:
        cmd = [
            'ffmpeg', '-y',
            '-ss', str(start),
            '-i', video_path,
            '-t', str(duration),
            '-filter_complex', filter_complex,
            '-map', '[out]',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            '-pix_fmt', 'yuv420p',
            output_path
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

        if proc.returncode != 0:
            logging.error(f"ffmpeg split-screen failed: {proc.stderr[:500]}")
            return False

        return os.path.exists(output_path) and os.path.getsize(output_path) > 0

    except Exception as e:
        logging.error(f"ffmpeg split-screen error: {e}")
        return False

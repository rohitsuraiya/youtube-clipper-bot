import subprocess
import re
import os
import json
import logging
import shutil
from dataclasses import dataclass
from typing import List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv("config.env")


@dataclass
class DetectedClip:
    start: float
    end: float
    score: float
    label: str


def _run_cmd(cmd: List[str], timeout: int = 600, capture_stdout: bool = False) -> str:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return proc.stdout if capture_stdout else proc.stderr
    except Exception as e:
        logging.error(f"cmd failed: {e}")
        return ""


def get_video_duration(video_path: str) -> float:
    out = _run_cmd([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path
    ], capture_stdout=True)
    try:
        return float(out.strip())
    except:
        return 0.0


def extract_audio(video_path: str, audio_path: str) -> bool:
    try:
        proc = subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1",
            audio_path
        ], capture_output=True, text=True, timeout=120)
        return proc.returncode == 0
    except:
        return False


def _split_audio_chunks(audio_path: str, chunk_dir: str, chunk_sec: int = 600) -> List[tuple]:
    os.makedirs(chunk_dir, exist_ok=True)
    duration_out = _run_cmd([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path
    ], capture_stdout=True)
    try:
        total = float(duration_out.strip())
    except:
        total = 0

    if total <= 0:
        return [(audio_path, 0)]

    chunks = []
    start = 0
    idx = 0
    while start < total:
        chunk_path = os.path.join(chunk_dir, f"chunk_{idx}.mp3")
        subprocess.run([
            "ffmpeg", "-y", "-i", audio_path,
            "-ss", str(start), "-t", str(chunk_sec),
            "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1",
            chunk_path
        ], capture_output=True, timeout=60)
        if os.path.exists(chunk_path) and os.path.getsize(chunk_path) > 0:
            chunks.append((chunk_path, start))
        start += chunk_sec
        idx += 1

    return chunks


def transcribe_with_groq(audio_path: str) -> Optional[List[dict]]:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        logging.info("No GROQ_API_KEY, skipping Groq transcription")
        return None

    chunk_dir = audio_path + "_chunks"
    chunks = _split_audio_chunks(audio_path, chunk_dir, chunk_sec=300)
    logging.info(f"Audio split into {len(chunks)} chunks for Groq Whisper")

    all_segments = []
    for i, (chunk_path, offset) in enumerate(chunks):
        for attempt in range(3):
            try:
                file_size = os.path.getsize(chunk_path)
                logging.info(f"Groq chunk {i} attempt {attempt+1} ({file_size} bytes)")
                with open(chunk_path, "rb") as f:
                    response = requests.post(
                        "https://api.groq.com/openai/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {api_key}"},
                        files={"file": (os.path.basename(chunk_path), f, "audio/mpeg")},
                        data={
                            "model": "whisper-large-v3",
                            "response_format": "verbose_json",
                        },
                        timeout=120,
                    )
                if response.status_code != 200:
                    logging.warning(f"Groq chunk {i} error: {response.status_code} {response.text[:200]}")
                    if attempt < 2:
                        import time
                        time.sleep(2 * (attempt + 1))
                    continue
                result = response.json()
                segments = result.get("segments", [])
                logging.info(f"Groq chunk {i} returned {len(segments)} segments")
                for seg in segments:
                    text = seg.get("text", "").strip()
                    if text and text not in ["...", "(silence)", ""]:
                        all_segments.append({
                            "start": seg.get("start", 0) + offset,
                            "end": seg.get("end", 0) + offset,
                            "text": text,
                        })
                break
            except Exception as e:
                logging.warning(f"Groq chunk {i} attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    import time
                    time.sleep(2 * (attempt + 1))

    if os.path.exists(chunk_dir):
        shutil.rmtree(chunk_dir, ignore_errors=True)

    logging.info(f"Groq total transcription segments: {len(all_segments)}")
    return all_segments if all_segments else None


def find_highlights_with_llm(segments: List[dict], video_title: str = "") -> Optional[List[dict]]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    transcript_text = ""
    for seg in segments:
        transcript_text += f"[{seg['start']:.1f} - {seg['end']:.1f}] {seg['text']}\n"

    logging.info(f"Sending {len(segments)} segments to GPT for highlight detection")

    prompt = f"""You are a viral video editor like Opus Clip. Analyze this transcript and find the most engaging, viral-worthy moments.

Video title: {video_title}

Transcript:
{transcript_text}

Find ALL engaging moments worth clipping (up to 10). For each moment, return:
- start: start time in seconds
- end: end time in seconds (15-60 seconds per clip, cut at natural sentence boundaries)
- reason: short reason (funny, emotional, dramatic, surprising, educational, etc.)

Rules:
- Complete thoughts only, never cut mid-sentence
- Include context before punchlines
- Variable length clips based on content
- Return ONLY valid JSON array, no extra text

Return format:
[{{"start": 0.0, "end": 30.0, "reason": "funny moment"}}, ...]"""

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-oss-120b:free",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=60,
        )
        if response.status_code != 200:
            logging.warning(f"LLM API error: {response.status_code}")
            return None
        content = response.json()["choices"][0]["message"]["content"].strip()
        logging.info(f"LLM response (first 500 chars): {content[:500]}")

        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        highlights = json.loads(content)
        logging.info(f"Parsed {len(highlights)} highlights from LLM")
        return highlights
    except Exception as e:
        logging.error(f"LLM highlight detection failed: {e}")
        return None


def smart_detect(video_path: str, top_n: int = 10) -> List[DetectedClip]:
    duration = get_video_duration(video_path)
    if duration <= 0:
        return []

    logging.info(f"Smart detect: duration={duration:.0f}s")

    silences = _detect_silence(video_path)
    loudness = _detect_loudness(video_path)
    scenes = _detect_scenes(video_path)

    segments = _score_segments(duration, silences, loudness, scenes)
    segments.sort(key=lambda x: x["combined_score"], reverse=True)

    selected = []
    used = []
    for seg in segments:
        score = seg["combined_score"]
        if score < 0.15:
            continue

        overlap = any(not (seg["end"] <= u["start"] or seg["start"] >= u["end"]) for u in used)
        if overlap:
            continue

        actual_start = max(0, seg["start"] - 2)
        actual_end = min(duration, seg["end"] + 2)
        actual_end = min(actual_end, actual_start + 60)
        actual_start = max(actual_start, actual_end - 60)

        length = actual_end - actual_start
        if length < 10:
            continue

        speech = seg.get("speech_ratio", 0)
        loud = seg.get("loud_score", 0)
        scene = seg.get("scene_score", 0)

        if speech > 0.7 and loud > 0.5:
            label = "Speaking + loud ({:.0f}s)".format(length)
        elif scene > 0.5:
            label = "Scene change ({:.0f}s)".format(length)
        elif loud > 0.6:
            label = "Audio peak ({:.0f}s)".format(length)
        elif speech > 0.7:
            label = "Speech segment ({:.0f}s)".format(length)
        else:
            label = "Clip ({:.0f}s)".format(length)

        selected.append(DetectedClip(
            start=actual_start, end=actual_end,
            score=score, label=label,
        ))
        used.append(seg)
        if len(selected) >= top_n:
            break

    selected.sort(key=lambda c: c.score, reverse=True)
    logging.info(f"Smart detect returning {len(selected)} clips")
    if not selected and duration > 0:
        selected.append(DetectedClip(start=0, end=min(30, duration), score=0.1, label="Clip ({:.0f}s)".format(min(30, duration))))
    return selected


def _detect_silence(video_path: str) -> List[Tuple[float, float]]:
    stderr = _run_cmd([
        "ffmpeg", "-i", video_path,
        "-af", "silencedetect=noise=-30dB:d=0.5",
        "-f", "null", "-"
    ], timeout=120)

    silences = []
    starts = []
    for line in stderr.splitlines():
        s_match = re.search(r"silence_start:\s*([\d.]+)", line)
        e_match = re.search(r"silence_end:\s*([\d.]+)", line)
        if s_match:
            starts.append(float(s_match.group(1)))
        if e_match and starts:
            silences.append((starts.pop(), float(e_match.group(1))))

    logging.info(f"Found {len(silences)} silent segments")
    return silences


def _detect_loudness(video_path: str) -> List[Tuple[float, float]]:
    stderr = _run_cmd([
        "ffmpeg", "-i", video_path,
        "-af", "ebur128=peak=true",
        "-f", "null", "-"
    ], timeout=120)

    loudness = []
    for line in stderr.splitlines():
        m = re.search(r"t:\s*([\d.]+)\s+M:\s*([-\d.]+)", line)
        if m:
            loudness.append((float(m.group(1)), float(m.group(2))))

    logging.info(f"Found {len(loudness)} loudness measurements")
    return loudness


def _detect_scenes(video_path: str) -> List[Tuple[float, float]]:
    stderr = _run_cmd([
        "ffmpeg", "-i", video_path,
        "-vf", "select='gt(scene,0.25)',showinfo",
        "-f", "null", "-"
    ], timeout=120)

    scenes = []
    for line in stderr.splitlines():
        t_m = re.search(r"pts_time:\s*([\d.]+)", line)
        s_m = re.search(r"scene:\s*([0-9.]+)", line)
        if t_m and s_m:
            scenes.append((float(t_m.group(1)), float(s_m.group(1))))

    logging.info(f"Found {len(scenes)} scene changes")
    return scenes


def _score_segments(
    duration: float,
    silences: List[Tuple[float, float]],
    loudness: List[Tuple[float, float]],
    scenes: List[Tuple[float, float]],
) -> List[dict]:
    step = 5.0
    segments = []
    t = 0.0
    while t < duration:
        end = min(t + step, duration)
        segments.append({"start": t, "end": end})
        t += step

    for seg in segments:
        s, e = seg["start"], seg["end"]

        speech_time = 0.0
        for ls, le in silences:
            overlap_start = max(s, ls)
            overlap_end = min(e, le)
            if overlap_start < overlap_end:
                speech_time += overlap_end - overlap_start
        seg_len = e - s
        speech_ratio = 1.0 - (speech_time / seg_len if seg_len > 0 else 0)

        loud_values = [lv for lt, lv in loudness if s <= lt <= e]
        if loud_values:
            avg_loud = sum(loud_values) / len(loud_values)
            loud_score = min(avg_loud / 20.0, 1.0)
        else:
            loud_score = 0.0

        scene_count = sum(1 for st, _ in scenes if s <= st <= e)
        scene_score = min(scene_count * 0.3, 1.0)

        combined = (speech_ratio * 0.4) + (loud_score * 0.35) + (scene_score * 0.25)
        seg["speech_ratio"] = speech_ratio
        seg["loud_score"] = loud_score
        seg["scene_score"] = scene_score
        seg["combined_score"] = combined

    return segments


def detect_clips(video_path: str, top_n: int = 10, **kwargs) -> List[DetectedClip]:
    duration = get_video_duration(video_path)
    if duration <= 0:
        return []

    video_title = kwargs.get("video_title", "")

    audio_path = video_path + "_audio.mp3"
    audio_extracted = extract_audio(video_path, audio_path)
    logging.info(f"Audio extracted: {audio_extracted}")

    if audio_extracted:
        try:
            segments = transcribe_with_groq(audio_path)
            if segments and len(segments) > 2:
                highlights = find_highlights_with_llm(segments, video_title)
                if highlights and len(highlights) > 0:
                    clips = []
                    for h in highlights[:top_n]:
                        start = max(0, h["start"])
                        end = min(duration, h["end"])
                        if end - start < 5:
                            continue
                        clips.append(DetectedClip(
                            start=start, end=end, score=0.8,
                            label=h.get("reason", "Engaging moment"),
                        ))
                    clips.sort(key=lambda c: c.score, reverse=True)
                    if clips:
                        logging.info(f"AI detected {len(clips)} clips")
                        return clips
                else:
                    logging.info("LLM returned no highlights, using transcript segmentation")
                    clips = _clips_from_transcript(segments, duration, top_n)
                    if clips:
                        return clips
            else:
                seg_count = len(segments) if segments else 0
                logging.info(f"Not enough speech segments ({seg_count}), using smart detect")
        except Exception as e:
            logging.error(f"AI pipeline failed: {e}")
        finally:
            if os.path.exists(audio_path):
                os.remove(audio_path)

    logging.info("Using smart audio/visual detection")
    return smart_detect(video_path, top_n)


def _clips_from_transcript(segments: List[dict], duration: float, top_n: int = 10) -> List[DetectedClip]:
    if not segments:
        return []

    clip_duration = 30.0
    clips = []
    i = 0
    while i < len(segments) and len(clips) < top_n:
        start = segments[i]["start"]
        end = start
        text_parts = []

        while i < len(segments) and end - start < clip_duration:
            end = segments[i]["end"]
            text_parts.append(segments[i]["text"])
            i += 1

        if end - start < 10:
            continue

        text = " ".join(text_parts)
        word_count = len(text.split())
        if word_count < 5:
            continue

        energy = min(word_count / 40.0, 1.0)
        clips.append(DetectedClip(
            start=max(0, start - 1),
            end=min(duration, end + 1),
            score=energy,
            label=f"Speech segment ({end - start:.0f}s)",
        ))

    clips.sort(key=lambda c: c.score, reverse=True)
    logging.info(f"Transcript segmentation produced {len(clips)} clips")
    return clips


def generate_youtube_seo(video_title: str, clip_label: str, clip_start: float, clip_end: float) -> dict:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return _fallback_seo(video_title, clip_start, clip_end)

    duration = int(clip_end - clip_start)
    prompt = f"""Generate a YouTube Shorts/Reels title, description and hashtags for this clip.

Original video: {video_title}
Clip moment: {clip_label}
Clip duration: {duration} seconds

Return ONLY valid JSON with these keys:
- "title": catchy YouTube title (max 100 chars, use emojis, power words)
- "description": 2-3 line engaging description (max 500 chars)
- "hashtags": 15 relevant hashtags (one string, space-separated with #)

Rules:
- Title must be catchy and clickbait-worthy
- Description must hook viewers in first line
- Hashtags must include broad + niche tags
- No extra text, ONLY the JSON"""

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-oss-120b:free",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": 500,
            },
            timeout=30,
        )
        if response.status_code != 200:
            logging.warning(f"SEO API error: {response.status_code}")
            return _fallback_seo(video_title, clip_start, clip_end)
        content = response.json()["choices"][0]["message"]["content"].strip()
        if "```" in content:
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        return json.loads(content)
    except Exception as e:
        logging.warning(f"SEO generation failed: {e}")
        return _fallback_seo(video_title, clip_start, clip_end)


def _fallback_seo(video_title: str, clip_start: float, clip_end: float) -> dict:
    duration = int(clip_end - clip_start)
    title = f"Wait For It... 🔥 ({duration}s)"
    description = f"From: {video_title}\nThis moment is INSANE! 😱"
    hashtags = "#shorts #viral #trending #fyp #youtube #funny #epic #wow #mustsee #clip #highlight #reels #short #amazing #mindblown"
    return {"title": title, "description": description, "hashtags": hashtags}


def generate_clip_list(
    video_path: str,
    clip_duration: float = 30.0,
    top_n: int = 10,
    **kwargs,
) -> Optional[List[DetectedClip]]:
    clips = detect_clips(video_path, top_n=top_n, **kwargs)
    if not clips:
        logging.info("No highlight clips detected")
        return []
    return clips

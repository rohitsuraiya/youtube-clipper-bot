import sqlite3
import os
import time

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'jobs.db')


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        url TEXT NOT NULL,
        style TEXT DEFAULT '',
        quality TEXT DEFAULT '',
        length TEXT DEFAULT '',
        face_tracking INTEGER DEFAULT 0,
        format TEXT DEFAULT '',
        status TEXT DEFAULT 'pending',
        clips_generated INTEGER DEFAULT 0,
        created_at REAL,
        completed_at REAL
    )''')
    conn.commit()
    conn.close()


def save_job(user_id, url, style='', quality='', length='',
             face_tracking=False, format='', status='pending'):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        '''INSERT INTO jobs (user_id, url, style, quality, length,
           face_tracking, format, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (user_id, url, style, quality, length,
         1 if face_tracking else 0, format, status, time.time())
    )
    job_id = cur.lastrowid
    conn.commit()
    conn.close()
    return job_id


def update_job(job_id, status=None, clips_generated=None):
    conn = sqlite3.connect(DB_PATH)
    if status and clips_generated is not None:
        conn.execute(
            'UPDATE jobs SET status=?, clips_generated=?, completed_at=? WHERE id=?',
            (status, clips_generated, time.time(), job_id)
        )
    elif status:
        conn.execute('UPDATE jobs SET status=? WHERE id=?', (status, job_id))
    elif clips_generated is not None:
        conn.execute(
            'UPDATE jobs SET clips_generated=?, completed_at=? WHERE id=?',
            (clips_generated, time.time(), job_id)
        )
    conn.commit()
    conn.close()


def get_user_jobs(user_id, limit=5):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        'SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?',
        (user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

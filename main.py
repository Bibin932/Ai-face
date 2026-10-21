import os, json, time, uuid, sqlite3, threading, hashlib
from pathlib import Path
from typing import Optional
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import JSONResponse
from deepface import DeepFace

BASE_DIR = Path(os.getenv('AI_DATA_DIR', './data')).resolve()
INBOX = BASE_DIR / 'inbox'
DB_PATH = BASE_DIR / 'face-index.sqlite3'
TOKEN = os.getenv('AI_SERVICE_TOKEN', '')
MODEL_NAME = os.getenv('FACE_MODEL', 'Facenet512')
DETECTOR_BACKEND = os.getenv('FACE_DETECTOR', 'opencv')
MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '25'))
QUEUE_SLEEP = float(os.getenv('QUEUE_SLEEP_SECONDS', '1.5'))
SIMILARITY_THRESHOLD = float(os.getenv('SIMILARITY_THRESHOLD', '0.32'))  # cosine distance; calibrate with real event photos
ALLOWED_EXTS = {'.jpg', '.jpeg', '.png', '.webp'}
BASE_DIR.mkdir(parents=True, exist_ok=True)
INBOX.mkdir(parents=True, exist_ok=True)
app = FastAPI(title='Cybercodix AI Face Index', version='1.0.0')
stop_event = threading.Event()
worker_thread = None


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    return con


def init_db():
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS media (
          media_id TEXT PRIMARY KEY, event_id TEXT NOT NULL, file_hash TEXT NOT NULL,
          file_path TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
          faces_count INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, UNIQUE(event_id, file_hash)
        );
        CREATE TABLE IF NOT EXISTS faces (
          id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL,
          media_id TEXT NOT NULL, face_index INTEGER NOT NULL,
          embedding_json TEXT NOT NULL, area_json TEXT,
          created_at TEXT NOT NULL, UNIQUE(media_id, face_index)
        );
        CREATE INDEX IF NOT EXISTS idx_faces_event ON faces(event_id);
        CREATE INDEX IF NOT EXISTS idx_media_status ON media(status);
        ''')


def auth(authorization: Optional[str]):
    if not TOKEN:
        raise HTTPException(503, 'AI_SERVICE_TOKEN is not configured; service is locked until configured.')
    if authorization != f'Bearer {TOKEN}':
        raise HTTPException(401, 'Invalid service token.')


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def cosine_distance(a, b):
    a = np.asarray(a, dtype=np.float32); b = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if not denom: return 1.0
    return float(1.0 - np.dot(a, b) / denom)


def worker_loop():
    while not stop_event.is_set():
        job = None
        try:
            with connect() as con:
                row = con.execute("SELECT * FROM media WHERE status IN ('queued','retry') ORDER BY created_at LIMIT 1").fetchone()
                if row:
                    con.execute("UPDATE media SET status='processing', updated_at=?, error=NULL WHERE media_id=?", (now(), row['media_id']))
                    job = dict(row)
            if not job:
                stop_event.wait(QUEUE_SLEEP); continue
            process_job(job)
        except Exception as exc:
            print(f'[worker] loop error: {exc}', flush=True)
            stop_event.wait(QUEUE_SLEEP)


def process_job(job):
    path = Path(job['file_path'])
    try:
        if not path.exists(): raise FileNotFoundError('Indexed image file is missing on AI worker.')
        reps = DeepFace.represent(img_path=str(path), model_name=MODEL_NAME,
            detector_backend=DETECTOR_BACKEND, enforce_detection=True, align=True)
        # Replace prior embeddings so retrying is idempotent.
        with connect() as con:
            con.execute('DELETE FROM faces WHERE media_id=?', (job['media_id'],))
            for i, rep in enumerate(reps):
                emb = rep.get('embedding')
                if not emb: continue
                area = rep.get('facial_area') or {}
                con.execute('INSERT OR REPLACE INTO faces(event_id,media_id,face_index,embedding_json,area_json,created_at) VALUES(?,?,?,?,?,?)',
                    (job['event_id'], job['media_id'], i, json.dumps(emb), json.dumps(area), now()))
            count = len(reps)
            con.execute("UPDATE media SET status='indexed', faces_count=?, updated_at=?, error=NULL WHERE media_id=?", (count, now(), job['media_id']))
        print(f"[worker] indexed media={job['media_id']} faces={count}", flush=True)
    except Exception as exc:
        with connect() as con:
            con.execute("UPDATE media SET status='failed', updated_at=?, error=? WHERE media_id=?", (now(), str(exc)[:1000], job['media_id']))
        print(f"[worker] failed media={job['media_id']}: {exc}", flush=True)


@app.on_event('startup')
def startup():
    global worker_thread
    init_db()
    stop_event.clear()
    worker_thread = threading.Thread(target=worker_loop, daemon=True, name='face-index-worker')
    worker_thread.start()


@app.on_event('shutdown')
def shutdown():
    stop_event.set()
    if worker_thread and worker_thread.is_alive(): worker_thread.join(timeout=3)


@app.get('/health')
def health():
    return {'ok': True, 'service': 'cybercodix-ai-index', 'model': MODEL_NAME, 'time': now()}


@app.post('/index')
async def enqueue_index(event_id: str = Form(...), media_id: str = Form(...), file: UploadFile = File(...), authorization: Optional[str] = Header(None)):
    auth(authorization)
    event_id = ''.join(c for c in event_id if c.isalnum() or c in '-_')[:80]
    media_id = ''.join(c for c in media_id if c.isalnum() or c in '-_')[:100]
    ext = Path(file.filename or '').suffix.lower()
    if not event_id or not media_id: raise HTTPException(400, 'event_id and media_id are required.')
    if ext not in ALLOWED_EXTS: raise HTTPException(415, 'Only JPG, PNG and WebP images can be face-indexed. Videos are skipped.')
    payload = await file.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if not payload: raise HTTPException(400, 'Empty file.')
    if len(payload) > MAX_UPLOAD_MB * 1024 * 1024: raise HTTPException(413, f'Image exceeds {MAX_UPLOAD_MB} MB.')
    digest = hashlib.sha256(payload).hexdigest()
    safe_event_dir = INBOX / event_id
    safe_event_dir.mkdir(parents=True, exist_ok=True)
    dest = safe_event_dir / f'{media_id}{ext}'
    if not dest.exists(): dest.write_bytes(payload)
    with connect() as con:
        existing = con.execute('SELECT media_id,status FROM media WHERE event_id=? AND file_hash=?', (event_id,digest)).fetchone()
        if existing:
            return {'ok': True, 'duplicate': True, 'mediaId': existing['media_id'], 'status': existing['status']}
        con.execute('INSERT OR IGNORE INTO media(media_id,event_id,file_hash,file_path,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
            (media_id,event_id,digest,str(dest),'queued',now(),now()))
    return JSONResponse(status_code=202, content={'ok': True, 'mediaId': media_id, 'eventId': event_id, 'status': 'queued'})


@app.post('/search')
async def search_faces(event_id: str = Form(...), selfie: UploadFile = File(...), authorization: Optional[str] = Header(None), limit: int = 60):
    auth(authorization)
    event_id = ''.join(c for c in event_id if c.isalnum() or c in '-_')[:80]
    ext = Path(selfie.filename or 'selfie.jpg').suffix.lower()
    if ext not in ALLOWED_EXTS: ext = '.jpg'
    payload = await selfie.read(MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if not payload or len(payload) > MAX_UPLOAD_MB * 1024 * 1024: raise HTTPException(400, 'Invalid or oversized selfie.')
    tmp = INBOX / f'query-{uuid.uuid4().hex}{ext}'
    tmp.write_bytes(payload)
    try:
        reps = DeepFace.represent(img_path=str(tmp), model_name=MODEL_NAME, detector_backend=DETECTOR_BACKEND, enforce_detection=True, align=True)
        if not reps: return {'eventId': event_id, 'matches': []}
        query_embedding = reps[0]['embedding']
        with connect() as con:
            rows = con.execute("SELECT f.media_id,f.embedding_json,f.area_json FROM faces f JOIN media m ON m.media_id=f.media_id WHERE f.event_id=? AND m.status='indexed'", (event_id,)).fetchall()
        best_by_media = {}
        for row in rows:
            dist = cosine_distance(query_embedding, json.loads(row['embedding_json']))
            if dist <= SIMILARITY_THRESHOLD:
                old = best_by_media.get(row['media_id'])
                if old is None or dist < old['distance']:
                    best_by_media[row['media_id']] = {'mediaId': row['media_id'], 'distance': round(dist, 5), 'similarity': round(max(0.0, 1.0-dist), 4), 'faceArea': json.loads(row['area_json'] or '{}')}
        matches = sorted(best_by_media.values(), key=lambda x: x['distance'])[:max(1,min(limit,100))]
        return {'eventId': event_id, 'model': MODEL_NAME, 'matches': matches, 'note': 'Similarity scores are not probabilities; threshold must be calibrated on representative event photos.'}
    except Exception as exc:
        raise HTTPException(422, f'Could not match this selfie. Use a clear, well-lit photo with one visible face. Details: {str(exc)[:250]}')
    finally:
        try: tmp.unlink(missing_ok=True)
        except Exception: pass


@app.get('/admin/stats')
def stats(authorization: Optional[str] = Header(None)):
    auth(authorization)
    with connect() as con:
        rows = con.execute('SELECT status,COUNT(*) n FROM media GROUP BY status').fetchall()
        faces = con.execute('SELECT COUNT(*) n FROM faces').fetchone()['n']
        total = con.execute('SELECT COUNT(*) n FROM media').fetchone()['n']
    counts = {r['status']: r['n'] for r in rows}
    return {'totalImages': total, 'queued': counts.get('queued',0)+counts.get('retry',0), 'processing': counts.get('processing',0), 'indexed': counts.get('indexed',0), 'failed': counts.get('failed',0), 'facesIndexed': faces, 'model': MODEL_NAME}


@app.post('/admin/retry-failed')
def retry_failed(authorization: Optional[str] = Header(None)):
    auth(authorization)
    with connect() as con:
        result = con.execute("UPDATE media SET status='retry', updated_at=?, error=NULL WHERE status='failed'", (now(),))
    return {'ok': True, 'requeued': result.rowcount}

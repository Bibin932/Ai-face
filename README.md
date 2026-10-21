# Cybercodix Instant Share — AI Face Indexing Engine

This bundle adds a separate Python AI service to detect faces in event photos, create embeddings, queue jobs in SQLite, and search an event's indexed photos using a guest selfie. It is designed to run on a VPS or Docker host, not inside Hostinger's standard Node.js web process.

## Architecture

- Existing website: Node.js/Express on Hostinger.
- AI service: Python + FastAPI + DeepFace on a VPS/container.
- Persistent engine data: `ai-engine/data/` (mount a persistent disk in production).
- Internal endpoints: `POST /index`, `POST /search`, `GET /admin/stats`, `POST /admin/retry-failed`.
- All AI endpoints except `/health` require `Authorization: Bearer <AI_SERVICE_TOKEN>`.

## Run with Docker

1. Copy `ai-engine/.env.example` to `ai-engine/.env` and set `AI_SERVICE_TOKEN` to a long random secret.
2. From the `ai-engine` folder run:

   ```bash
   docker build -t cybercodix-ai .
   docker run -d --name cybercodix-ai --restart unless-stopped \
     --env-file .env -p 127.0.0.1:8090:8090 \
     -v "$PWD/data:/app/data" cybercodix-ai
   ```

   This binds the service to localhost on the VPS. Put it behind the Node.js server/private network or a TLS reverse proxy; do not expose an unauthenticated AI service publicly.
3. The first image request may download model weights and take longer. Keep the persistent `data` directory.
4. Health check: `curl http://127.0.0.1:8090/health`.

Alternatively, use Python 3.11, install `requirements.txt`, set the environment variables, and run `uvicorn main:app --host 127.0.0.1 --port 8090`.

## Test indexing

```bash
curl -X POST http://127.0.0.1:8090/index \
  -H 'Authorization: Bearer YOUR_AI_SERVICE_TOKEN' \
  -F 'event_id=demo-event' -F 'media_id=photo-001' \
  -F 'file=@/path/to/consented-test-photo.jpg'
```

The API returns `202` with `status=queued`. The worker then indexes the image asynchronously. Check stats:

```bash
curl http://127.0.0.1:8090/admin/stats -H 'Authorization: Bearer YOUR_AI_SERVICE_TOKEN'
```

Search with a selfie from a person who has consented to this test:

```bash
curl -X POST http://127.0.0.1:8090/search \
  -H 'Authorization: Bearer YOUR_AI_SERVICE_TOKEN' \
  -F 'event_id=demo-event' -F 'selfie=@/path/to/consented-selfie.jpg'
```

Response contains media IDs and similarity distances. Your Node.js API must map `mediaId` values to the event's existing media index and return gallery URLs. Don't expose the AI token in frontend code.

## Connect the Node app

Set these environment variables on the Node service:

```env
AI_SERVICE_URL=http://YOUR_PRIVATE_AI_HOST:8090
AI_SERVICE_TOKEN=THE_SAME_LONG_RANDOM_SECRET
```

Use the code in `node-integration/ai-client.js`. Call `enqueueFaceIndex({eventId, mediaId, filePath, filename})` after an image has been safely saved and registered in `data/media-index.json` in both admin-upload and FTP-ingest paths. This sends a copy to the AI service. Video files should be skipped.

Use `searchEventFaces({eventId, selfieBuffer, filename})` in an authenticated/rate-limited Node route. Map returned `mediaId`s to `readIndex()` entries matching the same event ID and return `publicItem(item)` results. Keep the selfie only in memory and do not persist it.

If the Node app runs on Hostinger Web/Cloud and AI runs on a separate VPS, localhost will not point to the VPS. Configure a private, TLS-protected connection or a VPN/reverse proxy and firewall the service to trusted callers. Avoid public plain HTTP.

## Model / quality notes

- The starter defaults to DeepFace `Facenet512` and OpenCV face detection. It detects multiple faces in a photo and stores one embedding per detected face.
- Similarity thresholds are model-specific. `SIMILARITY_THRESHOLD=0.32` is a starting value, not a guaranteed correct threshold. Evaluate false matches and missed matches using representative, consented images from your actual venue conditions.
- Only JPEG/PNG/WebP images are accepted for indexing in this starter; videos and camera RAW files are skipped. Convert HEIC to JPEG before enqueueing if needed.
- This starter uses SQLite and one worker for a small pilot. For high volume, use PostgreSQL with pgvector and a durable queue (e.g. Redis/Celery), plus object storage.
- Secure face embeddings as sensitive biometric data. Provide clear notice/consent, access restrictions, a retention period, deletion workflows, and a non-face-search alternative. Similarity matching does not prove identity.
- DeepFace is an open-source framework; review the licenses of the selected model weights and dependencies for your intended commercial use before production.

## Included files

- `ai-engine/main.py`: API, queue, indexing worker, embedding database, selfie search.
- `ai-engine/requirements.txt`: Python dependencies.
- `ai-engine/Dockerfile`: container build.
- `node-integration/ai-client.js`: Node helper for indexing and search calls.
- `node-integration/server-integration.example.js`: example route/hook patterns to adapt to your actual server.js.

/* EXAMPLE ONLY: adapt these patterns to your existing server.js. Do not replace
   your server.js wholesale with this file. Keep AI_SERVICE_TOKEN server-side. */
import multer from 'multer';
import { enqueueFaceIndex, searchEventFaces, getFaceIndexStats } from './ai-client.js';

const selfieUpload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 8 * 1024 * 1024, files: 1 } });

// After an uploaded image has been saved and added to media-index.json:
// for (const item of added) {
//   if (item.kind === 'photo') enqueueFaceIndex({
//     eventId: item.eventId, mediaId: item.id,
//     filePath: path.join(UPLOADS, item.filename), filename: item.originalName
//   }).catch(err => console.error('AI enqueue failed:', err.message));
// }

// Public guest selfie search endpoint. Keep rate limiting enabled; add consent notice
// in the UI and map AI media IDs back to the current event's media-index items.
// app.post('/api/events/:eventId/face-search', selfieUpload.single('selfie'), async (req, res) => {
//   try {
//     if (!req.file) return res.status(400).json({ error: 'Please provide a selfie image.' });
//     const result = await searchEventFaces({ eventId: req.params.eventId, selfieBuffer: req.file.buffer, filename: req.file.originalname });
//     const ids = new Set(result.matches.map(m => m.mediaId));
//     const items = readIndex().filter(item => item.eventId === req.params.eventId && ids.has(item.id)).map(publicItem);
//     res.json({ eventId: req.params.eventId, items, matches: result.matches });
//   } catch (err) { res.status(503).json({ error: 'Face search is temporarily unavailable.', detail: err.message }); }
// });

// Admin-only stats example; keep adminAuthorized() from your existing server.js.
// app.get('/api/admin/ai-stats', async (req, res) => {
//   if (!adminAuthorized(req)) return res.status(401).json({ error: 'Unauthorized' });
//   try { res.json(await getFaceIndexStats()); }
//   catch (err) { res.status(503).json({ error: 'AI engine unavailable', detail: err.message }); }
// });

// Browser uploads over the same resumable protocol the phone uses
// (/api/sync/uploads/*): hash, reserve in batches, send in chunks, complete.
// Files the library already has are answered at reservation and never sent;
// an interrupted transfer resumes from the server's offset when the same
// files are chosen again in this browser session.

const RESERVE_BATCH = 16;
const PARALLEL_FILES = 3;
const HASH_WORKERS = 2;
const MAX_ATTEMPTS = 5;

class UploadStopped extends Error {}

class Channel {
  constructor() { this.items = []; this.waiters = []; this.closed = false; }
  push(item) {
    const waiter = this.waiters.shift();
    if (waiter) waiter(item); else this.items.push(item);
  }
  close() {
    this.closed = true;
    this.waiters.splice(0).forEach((waiter) => waiter(undefined));
  }
  async take() {
    if (this.items.length) return this.items.shift();
    if (this.closed) return undefined;
    return new Promise((resolve) => this.waiters.push(resolve));
  }
  drain(max) { return this.items.splice(0, max); }
}

function fnv1a(text) {
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

function relativePath(file) {
  return file.webkitRelativePath || file.name;
}

function sourceOf(file) {
  const path = file.webkitRelativePath || '';
  const folder = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
  if (!folder) return undefined;
  const kind = file.type.startsWith('video/') ? 'video' : file.type.startsWith('image/') ? 'image' : '';
  return {
    id: `web:${folder}`.slice(0, 512),
    name: folder.split('/').pop(),
    relative_path: folder,
    media_kind: kind,
  };
}

function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }

async function errorText(response) {
  try {
    const body = await response.json();
    return body.detail || body.error_message || `HTTP ${response.status}`;
  } catch (_) {
    return `HTTP ${response.status}`;
  }
}

class HashPool {
  constructor(size, onBytes) {
    this.onBytes = onBytes;
    this.pending = new Map();
    this.next = 0;
    this.workers = Array.from({ length: size }, () => {
      const worker = new Worker('/static/sha256_worker.js?v=1');
      worker.onmessage = (event) => this.receive(event.data);
      return worker;
    });
  }
  receive({ id, bytes, sha256, error }) {
    if (bytes) { this.onBytes(bytes); return; }
    const pending = this.pending.get(id);
    if (!pending) return;
    this.pending.delete(id);
    if (error) pending.reject(new Error(error)); else pending.resolve(sha256);
  }
  hash(file, workerIndex) {
    const id = ++this.next;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.workers[workerIndex].postMessage({ id, file });
    });
  }
  close() { this.workers.forEach((worker) => worker.terminate()); }
}

/**
 * Upload ``files`` into the signed-in account.
 * ``onProgress(stats)`` is called as work advances; the returned promise
 * resolves with the final stats. ``signal`` (AbortSignal) stops the run;
 * what was sent stays on the server and resumes on the next run.
 */
export async function uploadFiles(files, { onProgress = () => {}, signal } = {}) {
  const stats = {
    total: files.length, totalBytes: files.reduce((sum, file) => sum + file.size, 0),
    hashedBytes: 0, sentBytes: 0, resumedBytes: 0, done: 0, sent: 0, duplicates: 0,
    failed: [], bytesPerSecond: 0, stoppedReason: '',
  };
  const samples = [];
  const report = () => {
    const now = performance.now();
    samples.push([now, stats.sentBytes]);
    while (samples.length > 2 && now - samples[0][0] > 5000) samples.shift();
    const [t0, b0] = samples[0];
    stats.bytesPerSecond = now > t0 ? ((stats.sentBytes - b0) * 1000) / (now - t0) : 0;
    onProgress({ ...stats, failed: stats.failed.slice() });
  };
  let stopped = null;
  const stop = (reason) => { if (!stopped) stopped = reason; };
  signal?.addEventListener('abort', () => stop('Envio interrompido.'));
  const check = () => { if (stopped) throw new UploadStopped(stopped); };

  const fail = (entry, message) => {
    stats.failed.push({ name: relativePath(entry.file), message });
    stats.done += 1;
    report();
  };
  const settle = (state) => {
    if (state === 'duplicate') stats.duplicates += 1; else stats.sent += 1;
    stats.done += 1;
    report();
  };

  // Stage 1: hash. Stage 2: reserve in batches. Stage 3: send and complete.
  const hashed = new Channel();
  const reserved = new Channel();
  const pool = new HashPool(HASH_WORKERS, (bytes) => { stats.hashedBytes += bytes; report(); });
  let nextFile = 0;

  async function hashLoop(workerIndex) {
    while (!stopped && nextFile < files.length) {
      const file = files[nextFile++];
      try {
        const sha256 = await pool.hash(file, workerIndex);
        hashed.push({ file, sha256 });
      } catch (error) {
        fail({ file }, `Não foi possível ler o arquivo: ${error.message}`);
      }
    }
  }

  async function reserveLoop() {
    for (;;) {
      const first = await hashed.take();
      if (first === undefined || stopped) break;
      const batch = [first, ...hashed.drain(RESERVE_BATCH - 1)];
      const uploads = batch.map((entry) => {
        entry.clientId = `web:${entry.sha256.slice(0, 48)}:${fnv1a(relativePath(entry.file))}`;
        const item = {
          client_upload_id: entry.clientId, filename: entry.file.name, size: entry.file.size,
          sha256: entry.sha256, captured_at: new Date(entry.file.lastModified || Date.now()).toISOString(),
        };
        const source = sourceOf(entry.file);
        if (source) item.source = source;
        return item;
      });
      const response = await request('/api/sync/uploads/batch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uploads }),
      });
      if (!response.ok) {
        const message = await errorText(response);
        batch.forEach((entry) => fail(entry, message));
        continue;
      }
      const byClient = new Map((await response.json()).uploads.map((item) => [item.client_upload_id, item]));
      for (const entry of batch) {
        const answer = byClient.get(entry.clientId);
        if (!answer || answer.error_code) {
          fail(entry, answer ? answer.error_message : 'Resposta incompleta do servidor');
        } else if (answer.state === 'uploading') {
          Object.assign(entry, { uploadId: answer.upload_id, offset: answer.offset, chunkSize: answer.chunk_size });
          stats.resumedBytes += answer.offset; // already on the server from an earlier run
          reserved.push(entry);
        } else {
          // Already in the library: found by content now, or finished on an earlier run.
          settle('duplicate');
        }
      }
    }
    reserved.close();
  }

  async function request(url, init) {
    for (let attempt = 1; ; attempt++) {
      check();
      let response;
      try {
        response = await fetch(url, { credentials: 'same-origin', ...init });
      } catch (error) {
        if (attempt >= MAX_ATTEMPTS) throw error;
        await sleep(500 * 2 ** attempt);
        continue;
      }
      if (response.status === 401) {
        stop('A sessão terminou (saiu ou este navegador foi desconectado). Entre de novo para continuar.');
        check();
      }
      if (response.status >= 500 && response.status !== 507 && attempt < MAX_ATTEMPTS) {
        await sleep(500 * 2 ** attempt);
        continue;
      }
      return response;
    }
  }

  async function send(entry) {
    const { file } = entry;
    while (entry.offset < file.size) {
      const end = Math.min(file.size, entry.offset + entry.chunkSize);
      const response = await request(`/api/sync/uploads/${entry.uploadId}?offset=${entry.offset}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/octet-stream' }, body: file.slice(entry.offset, end),
      });
      if (response.ok) {
        const next = (await response.json()).offset ?? end;
        stats.sentBytes += next - entry.offset;
        entry.offset = next;
        report();
        continue;
      }
      if (response.status !== 409) throw new Error(await errorText(response));
      // Offset mismatch (a retried chunk that had landed, say): ask the server.
      const status = await request(`/api/sync/uploads/${entry.uploadId}`, {});
      if (!status.ok) throw new Error(await errorText(status));
      const body = await status.json();
      if (body.state === 'finalizing') break;
      if (body.state !== 'uploading') return body.state;
      stats.sentBytes += Math.max(0, body.offset - entry.offset);
      entry.offset = body.offset;
    }
    const completed = await request(`/api/sync/uploads/${entry.uploadId}/complete`, { method: 'POST' });
    if (!completed.ok) throw new Error(await errorText(completed));
    const body = await completed.json();
    if (body.state === 'failed_processing') throw new Error('O servidor não conseguiu processar a mídia');
    return body.state;
  }

  async function sendLoop() {
    for (;;) {
      const entry = await reserved.take();
      if (entry === undefined || stopped) return;
      try {
        settle(await send(entry));
      } catch (error) {
        if (error instanceof UploadStopped) return;
        fail(entry, error.message);
      }
    }
  }

  report();
  const hashing = Promise.all(Array.from({ length: HASH_WORKERS }, (_, i) => hashLoop(i)))
    .finally(() => { hashed.close(); pool.close(); });
  const reserving = reserveLoop().catch((error) => {
    if (!(error instanceof UploadStopped)) stop(`Falha ao reservar envios: ${error.message}`);
    reserved.close();
  });
  await Promise.all([hashing, reserving, ...Array.from({ length: PARALLEL_FILES }, sendLoop)]);
  stats.stoppedReason = stopped || '';
  report();
  return stats;
}

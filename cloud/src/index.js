/**
 * scirocco-dash -- Cloudflare Worker behind the drive-log dashboard.
 *
 * Routes:
 *   GET /                       dashboard page (served by the assets layer;
 *                               the handler below is only a fallback)
 *   GET /api/drives             list committed sessions, newest first
 *   GET /api/drive/:id/:file    stream one session object from R2
 *   GET /api/tile/:z/:x/:y      OpenStreetMap raster tile, proxied + cached
 *   GET /api/markers/:id        this drive's marker document
 *   PUT /api/markers/:id        merge markers into that document
 *
 * R2 layout (written by the car's uploader, see docs/PLAN-deck.md):
 *   drives/YYYY/MM/<session_id>/{drive.csv.gz, bursts.csv.gz,
 *                                raw.bin.gz, meta.json, markers.json}
 * markers.json is the only object this Worker ever WRITES, and its key is
 * built from a session id that matched SESSION_ID_RE -- no request-supplied
 * string reaches the key.
 * meta.json is uploaded LAST and is the commit marker: a session directory
 * without one is a partial upload (car lost WiFi mid-push) and must not be
 * listed. session_id starts with YYYY-MM-DD_HHMM, which is what lets
 * /api/drive derive the YYYY/MM path segments from the id alone.
 *
 * Free-tier notes: /api/drives is a paged R2 list (one class-A op per 1000
 * objects -- a decade of drives is still a single page); everything else is
 * a single GET per request. No bundler, no dependencies.
 */

// Session ids come from the tee: 2026-08-22_0731. The trailing group
// tolerates a disambiguating suffix (e.g. _0731b) without allowing "/" or
// ".." anywhere near an R2 key.
const SESSION_ID_RE = /^\d{4}-\d{2}-\d{2}_\d{4}[0-9A-Za-z-]*$/;

// Only these objects are ever served to the browser. raw.bin.gz stays
// bucket-only on purpose: it is the big archive, not dashboard data.
const SERVED_FILES = {
  "drive.csv.gz": { type: "text/csv; charset=utf-8", gzip: true },
  "bursts.csv.gz": { type: "text/csv; charset=utf-8", gzip: true },
  "meta.json": { type: "application/json; charset=utf-8", gzip: false },
};

// has_bursts heuristic, from the listing alone (no per-session GETs): a
// header-only bursts.csv.gz is a couple hundred bytes; the shortest real
// pull (5 s pre-roll + a few seconds at ~14 Hz) gzips to tens of KB.
const BURSTS_MIN_BYTES = 1024;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const method = request.method;
    const read = method === "GET" || method === "HEAD";

    // PUT is allowed on exactly one route (the marker document); everything
    // else on this Worker is read-only.
    const mk = path.match(/^\/api\/markers\/([^/]+)$/);
    if (!read && !(method === "PUT" && mk)) {
      return jsonError(405, "GET only (PUT allowed on /api/markers/:id)");
    }

    try {
      if (mk) {
        const id = decodeURIComponent(mk[1]);
        return method === "PUT"
          ? await putMarkers(env, request, id)
          : await getMarkers(env, id);
      }
      if (path === "/api/drives") {
        return await listDrives(env);
      }
      const t = path.match(/^\/api\/tile\/(\d+)\/(\d+)\/(\d+)(?:\.png)?$/);
      if (t) {
        return await serveTile(request, ctx, +t[1], +t[2], +t[3]);
      }
      const m = path.match(/^\/api\/drive\/([^/]+)\/([^/]+)$/);
      if (m) {
        return await serveObject(env, request,
          decodeURIComponent(m[1]), decodeURIComponent(m[2]));
      }
      if (path.startsWith("/api/")) {
        return jsonError(404, "no such route");
      }
      // Anything else belongs to the static assets layer. With the default
      // assets config this code only runs for paths that matched no asset,
      // but routing "/" through here too keeps the Worker correct if
      // run_worker_first is ever enabled.
      return await env.ASSETS.fetch(request);
    } catch (e) {
      return jsonError(500, String(e && e.message || e));
    }
  },
};

/**
 * GET /api/drives -> [{id, date, size, has_bursts}], newest first.
 *
 * Derived entirely from the R2 listing: a session appears once its
 * meta.json commit marker exists; size is the byte total of the session's
 * objects (raw.bin.gz included -- it is the honest "what this drive costs
 * in the bucket" number); date comes from the session id, falling back to
 * meta.json's upload time if an id ever stops parsing.
 */
async function listDrives(env) {
  const sessions = new Map();
  let cursor;
  do {
    const page = await env.LOGS.list({ prefix: "drives/", cursor, limit: 1000 });
    for (const obj of page.objects) {
      const m = obj.key.match(/^drives\/\d{4}\/\d{2}\/([^/]+)\/([^/]+)$/);
      if (!m) continue;
      const [, id, file] = m;
      let s = sessions.get(id);
      if (!s) {
        s = { id, size: 0, committed: false, burstsBytes: 0, uploaded: null };
        sessions.set(id, s);
      }
      s.size += obj.size;
      if (file === "meta.json") {
        s.committed = true;
        s.uploaded = obj.uploaded;
      } else if (file === "bursts.csv.gz") {
        s.burstsBytes = obj.size;
      }
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);

  const drives = [...sessions.values()]
    .filter((s) => s.committed)
    .map((s) => ({
      id: s.id,
      date: dateFromId(s.id) ||
        (s.uploaded ? new Date(s.uploaded).toISOString() : ""),
      size: s.size,
      has_bursts: s.burstsBytes > BURSTS_MIN_BYTES,
    }))
    // Session ids are zero-padded YYYY-MM-DD_HHMM, so a plain string sort
    // is a chronological sort.
    .sort((a, b) => (a.id < b.id ? 1 : -1));

  return new Response(JSON.stringify(drives), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      // The car may commit a new drive while the page is open.
      "cache-control": "no-store",
    },
  });
}

/** "2026-08-22_0731" -> "2026-08-22 07:31" (display string, local car time). */
function dateFromId(id) {
  const m = id.match(/^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})/);
  return m ? `${m[1]} ${m[2]}:${m[3]}` : null;
}

/**
 * GET /api/drive/:id/:file -- stream one R2 object.
 *
 * The .csv.gz objects are stored gzipped and served byte-for-byte with
 * Content-Encoding: gzip + encodeBody "manual", so the runtime passes the
 * stored bytes through untouched and the browser's fetch() gunzips for
 * free. The page still keeps a DecompressionStream fallback for any path
 * (proxy, download tool) that strips the header. meta.json is stored and
 * served plain.
 */
async function serveObject(env, request, id, file) {
  if (!SESSION_ID_RE.test(id)) {
    return jsonError(400, "bad session id");
  }
  const spec = SERVED_FILES[file];
  if (!spec) {
    return jsonError(404, "no such file");
  }
  const key = `drives/${id.slice(0, 4)}/${id.slice(5, 7)}/${id}/${file}`;

  // onlyIf honors the browser's If-None-Match revalidation: R2 returns the
  // object without a body when the etag still matches.
  const obj = await env.LOGS.get(key, { onlyIf: request.headers });
  if (obj === null) {
    return jsonError(404, "not found");
  }
  const headers = {
    etag: obj.httpEtag,
    // Session objects are immutable once meta.json lands; a day of browser
    // cache keeps re-opens instant without making dev annoying.
    "cache-control": "public, max-age=86400",
    "content-type": spec.type,
  };
  if (obj.body === undefined || obj.body === null) {
    return new Response(null, { status: 304, headers: { etag: obj.httpEtag } });
  }
  if (spec.gzip) {
    headers["content-encoding"] = "gzip";
    // encodeBody "manual" = "the body already matches Content-Encoding" --
    // without it the runtime would gzip the gzip.
    return new Response(obj.body, { headers, encodeBody: "manual" });
  }
  return new Response(obj.body, { headers });
}

function jsonError(status, message) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

/* ======================================================================
   TILE PROXY -- GET /api/tile/:z/:x/:y

   The browser never talks to tile.openstreetmap.org directly, for two
   reasons that both matter:

   1. OSM's tile usage policy requires a *identifying* User-Agent naming the
      application and a way to contact its operator. A browser sends its own
      UA and we cannot change it; a Worker can. TILE_UA below is that
      identification -- keep it truthful if this is ever forked.
   2. Proxying puts every tile behind the Cloudflare edge cache, so a second
      look at the same drive (or a second viewer) costs OSM nothing. The
      policy asks for exactly this.

   z is clamped to 3..19 and x/y are validated against 2^z, so the upstream
   URL is always a well-formed tile coordinate and this route can never be
   turned into an open proxy for arbitrary paths.

   An upstream failure returns a TRANSPARENT tile with 200, not an error:
   the page draws the track on top of whatever the basemap layer produced,
   and a 200 empty tile degrades to exactly the "basemap off" look for that
   square instead of leaving a broken-image hole.
   ====================================================================== */
const TILE_HOST = "https://tile.openstreetmap.org";
// CHANGE ME: the OpenStreetMap tile usage policy requires a real contact.
const TILE_UA =
  "scirocco-dash/1.0 (Scirocco drive-log dashboard; " +
  "+https://scirocco.example.com; contact: you@example.com)";
const TILE_Z_MIN = 3;
const TILE_Z_MAX = 19;
// Tiles are effectively immutable for our purposes; a week at the edge and
// a week in the browser is well inside what the OSM policy asks for.
const TILE_MAX_AGE = 604800;
// 1x1 fully transparent PNG. drawImage() stretches it to the tile square.
const BLANK_PNG_B64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNgAAIAAAUAAen" +
  "63NgAAAAASUVORK5CYII=";
let BLANK_PNG = null;

function blankTile(reason) {
  if (BLANK_PNG === null) {
    const bin = atob(BLANK_PNG_B64);
    BLANK_PNG = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) BLANK_PNG[i] = bin.charCodeAt(i);
  }
  return new Response(BLANK_PNG, {
    status: 200,
    headers: {
      "content-type": "image/png",
      // Short, so a transient upstream hiccup is retried on the next pan
      // instead of being cached as a hole for a week.
      "cache-control": "public, max-age=60",
      "x-tile": "blank:" + reason,
    },
  });
}

async function serveTile(request, ctx, z, x, y) {
  if (!Number.isInteger(z) || z < TILE_Z_MIN || z > TILE_Z_MAX) {
    return jsonError(400, "zoom out of range (" + TILE_Z_MIN + ".." +
      TILE_Z_MAX + ")");
  }
  const span = 2 ** z;
  if (!Number.isInteger(x) || !Number.isInteger(y) ||
      x < 0 || y < 0 || x >= span || y >= span) {
    return jsonError(400, "tile out of range for zoom " + z);
  }

  const upstream = `${TILE_HOST}/${z}/${x}/${y}.png`;
  // Canonical cache key: our own path, so the entry is shared by every
  // viewer regardless of how they spelled the request.
  const origin = new URL(request.url).origin;
  const cacheKey = new Request(`${origin}/api/tile/${z}/${x}/${y}`,
    { method: "GET" });

  let cache = null;
  try { cache = caches.default; } catch (e) { /* no cache in this runtime */ }
  if (cache) {
    const hit = await cache.match(cacheKey);
    if (hit) return hit;
  }

  let res;
  try {
    res = await fetch(upstream, {
      headers: {
        "user-agent": TILE_UA,
        referer: "https://scirocco.example.com/",
        accept: "image/png,image/*;q=0.8",
      },
      // Let Cloudflare's own fetch cache help too; harmless if ignored.
      cf: { cacheTtl: TILE_MAX_AGE, cacheEverything: true },
    });
  } catch (e) {
    return blankTile("fetch-failed");
  }
  if (!res.ok) return blankTile("upstream-" + res.status);

  const body = await res.arrayBuffer();
  const out = new Response(body, {
    status: 200,
    headers: {
      "content-type": res.headers.get("content-type") || "image/png",
      "cache-control": `public, max-age=${TILE_MAX_AGE}, immutable`,
      "x-tile": "osm",
    },
  });
  if (cache) {
    const stash = out.clone();
    if (ctx && ctx.waitUntil) ctx.waitUntil(cache.put(cacheKey, stash));
    else await cache.put(cacheKey, stash).catch(() => {});
  }
  return out;
}

/* ======================================================================
   MARKER DOCUMENT -- GET/PUT /api/markers/:id

   One JSON object per drive at drives/YYYY/MM/<session>/markers.json, so a
   marker dropped on a laptop is there on the phone and in whatever
   browser the tuner opens the shared link in. The document is small by
   construction (caps below) and is the ONLY thing this Worker writes.

   Concurrency: the write is read-merge-write under an R2 etag precondition,
   retried a few times. Merge is last-write-wins per marker id using the
   client's `updated` stamp, so two tabs editing different markers both keep
   their edit and two tabs editing the SAME marker settle on the later one.
   A delete is a tombstone ({deleted:true}) rather than an omission --
   without it, a stale tab's PUT would resurrect everything it deleted.
   ====================================================================== */
const MK_MAX_BODY = 65536;       // bytes of request body
const MK_MAX_COUNT = 200;        // markers kept per drive (tombstones incl.)
const MK_MAX_NAME = 60;          // characters
const MK_MAX_NOTE = 500;         // characters
const MK_ID_RE = /^[A-Za-z0-9_-]{1,24}$/;
const MK_TOMBSTONE_MS = 30 * 24 * 3600 * 1000;
/* Colour whitelist == the page's categorical slots MINUS the amber
   (#c98500) that means "burst" everywhere on the dashboard. A marker can
   never be painted in the colour that already means something else. */
const MK_COLORS = new Set([
  "#3987e5", "#d95926", "#199e70", "#d55181",
  "#008300", "#9085e9", "#e66767",
]);

function markersKey(id) {
  return `drives/${id.slice(0, 4)}/${id.slice(5, 7)}/${id}/markers.json`;
}

/** Reject anything that is not a plain, in-range marker. Returns a clean
    object built field by field -- nothing from the request is copied
    wholesale, so an unexpected key cannot ride along into R2. */
function cleanMarker(raw, nowMs) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  if (typeof raw.id !== "string" || !MK_ID_RE.test(raw.id)) return null;

  const updated = Number.isFinite(raw.updated)
    ? Math.min(Math.max(+raw.updated, 0), nowMs + 60000) : nowMs;

  if (raw.deleted === true) {
    return { id: raw.id, deleted: true, updated };
  }
  const t0 = +raw.t0;
  if (!Number.isFinite(t0)) return null;
  let t1 = raw.t1 === null || raw.t1 === undefined ? null : +raw.t1;
  if (t1 !== null && (!Number.isFinite(t1) || t1 < t0)) return null;
  if (t1 !== null && t1 === t0) t1 = null;      // zero-width range = instant

  const color = typeof raw.color === "string" ? raw.color.toLowerCase() : "";
  if (!MK_COLORS.has(color)) return null;

  const name = typeof raw.name === "string"
    ? raw.name.slice(0, MK_MAX_NAME) : "";
  const note = typeof raw.note === "string"
    ? raw.note.slice(0, MK_MAX_NOTE) : "";

  return { id: raw.id, t0, t1, name, color, note, updated };
}

function emptyDoc() {
  return { v: 1, updated: 0, markers: [] };
}

async function readDoc(env, key) {
  const obj = await env.LOGS.get(key);
  if (!obj) return { doc: emptyDoc(), etag: null };
  let doc;
  try { doc = JSON.parse(await obj.text()); } catch (e) { doc = null; }
  if (!doc || !Array.isArray(doc.markers)) doc = emptyDoc();
  return { doc, etag: obj.etag };
}

/** Last-write-wins per id, tombstones pruned, count capped. */
function mergeDocs(current, incoming, nowMs) {
  const byId = new Map();
  for (const m of current) byId.set(m.id, m);
  for (const m of incoming) {
    const prev = byId.get(m.id);
    if (!prev || m.updated >= prev.updated) byId.set(m.id, m);
  }
  let out = [...byId.values()].filter(
    (m) => !(m.deleted && nowMs - m.updated > MK_TOMBSTONE_MS));
  if (out.length > MK_MAX_COUNT) {
    // Drop tombstones first, then the oldest edits: a live marker is always
    // worth more than a record of a deleted one.
    out.sort((a, b) => (a.deleted ? 0 : 1) - (b.deleted ? 0 : 1) ||
      a.updated - b.updated);
    out = out.slice(out.length - MK_MAX_COUNT);
  }
  out.sort((a, b) => (a.t0 || 0) - (b.t0 || 0));
  return out;
}

async function getMarkers(env, id) {
  if (!SESSION_ID_RE.test(id)) return jsonError(400, "bad session id");
  const { doc } = await readDoc(env, markersKey(id));
  return new Response(JSON.stringify(doc), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      // Markers change while the page is open; never serve a stale set.
      "cache-control": "no-store",
    },
  });
}

async function putMarkers(env, request, id) {
  if (!SESSION_ID_RE.test(id)) return jsonError(400, "bad session id");

  const declared = +request.headers.get("content-length");
  if (Number.isFinite(declared) && declared > MK_MAX_BODY) {
    return jsonError(413, "payload too large");
  }
  const text = await request.text();
  if (text.length > MK_MAX_BODY) return jsonError(413, "payload too large");

  let body;
  try { body = JSON.parse(text); } catch (e) {
    return jsonError(400, "body is not JSON");
  }
  const list = body && Array.isArray(body.markers) ? body.markers : null;
  if (!list) return jsonError(400, "expected {markers:[...]}");
  if (list.length > MK_MAX_COUNT) return jsonError(400, "too many markers");

  const nowMs = Date.now();
  const incoming = [];
  for (const raw of list) {
    const m = cleanMarker(raw, nowMs);
    if (!m) return jsonError(400, "invalid marker in payload");
    incoming.push(m);
  }

  const key = markersKey(id);
  const meta = {
    httpMetadata: { contentType: "application/json; charset=utf-8" },
  };
  let merged = null;
  for (let attempt = 0; attempt < 4 && merged === null; attempt++) {
    const { doc, etag } = await readDoc(env, key);
    const markers = mergeDocs(doc.markers, incoming, nowMs);
    const next = { v: 1, updated: nowMs, markers };
    const payload = JSON.stringify(next);
    let res;
    try {
      res = await env.LOGS.put(key, payload,
        etag ? { ...meta, onlyIf: { etagMatches: etag } } : meta);
    } catch (e) {
      // A runtime without onlyIf support must not lose the write.
      res = await env.LOGS.put(key, payload, meta);
    }
    if (res !== null) merged = next;      // null == precondition lost a race
  }
  if (merged === null) return jsonError(409, "conflict, please retry");

  return new Response(JSON.stringify(merged), {
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

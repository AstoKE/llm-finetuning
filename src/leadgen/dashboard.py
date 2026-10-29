"""Builds a self-contained local HTML dashboard from the raw Places JSONL files.

Nothing is sent anywhere: the data is embedded in the generated file, which has no external requests.
"""

import json
import re
from datetime import datetime
from pathlib import Path

VENDOR = Path(__file__).parent / "vendor" / "leaflet"


def _read(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def merge(ids: list[dict], pro: list[dict], enterprise: list[dict]) -> list[dict]:
    """One flat record per place; later tiers overwrite earlier ones, and ID-only places stay listed."""
    places: dict[str, dict] = {}
    for row in ids:
        places[row["place_id"]] = {"id": row["place_id"], "queries": row.get("queries", []), "detail": False}
    for row in pro + enterprise:
        place = places.setdefault(row["id"], {"id": row["id"], "queries": [], "detail": True})
        loc = row.get("location") or {}
        place.update(
            detail=True,
            name=(row.get("displayName") or {}).get("text") or place.get("name"),
            type=row.get("primaryType") or place.get("type"),
            status=row.get("businessStatus") or place.get("status"),
            address=row.get("formattedAddress") or place.get("address"),
            lat=loc.get("latitude", place.get("lat")),
            lng=loc.get("longitude", place.get("lng")),
            url=row.get("googleMapsUri") or place.get("url"),
            phone=row.get("nationalPhoneNumber") or place.get("phone"),
            web=row.get("websiteUri") or place.get("web"),
            rating=row.get("rating", place.get("rating")),
            ratings=row.get("userRatingCount", place.get("ratings")),
        )
    return list(places.values())


def load(ids_path: Path, pro_path: Path | None, enterprise_path: Path | None) -> list[dict]:
    return merge(_read(ids_path), _read(pro_path), _read(enterprise_path))


def render(places: list[dict], live: bool = False) -> str:
    """`live` pages start empty and pull data and progress from the local `leadgen serve` API."""
    data = json.dumps([] if live else places, ensure_ascii=False).replace("</", "<\\/")
    # Data goes in last so place names can never be mistaken for a placeholder.
    return (
        TEMPLATE.replace("__LIVE__", "true" if live else "false")
        .replace("__LEAFLET_CSS__", (VENDOR / "leaflet.css").read_text(encoding="utf-8"))
        .replace("__LEAFLET_JS__", (VENDOR / "leaflet.js").read_text(encoding="utf-8"))
        .replace("__DATA__", data)
    )


def build(ids_path: Path, pro_path: Path | None, enterprise_path: Path | None, live: bool = False) -> str:
    return render([] if live else load(ids_path, pro_path, enterprise_path), live)


REPORT_NAME = re.compile(r"^report-\d{8}-\d{6}-\d+\.html$")


def save_report(raw: Path, reports: Path, now: datetime | None = None) -> Path:
    """Static snapshot of the current data; the place count is part of the file name for listing."""
    places = load(raw / "place_ids.jsonl", raw / "details_pro.jsonl", raw / "details_enterprise.jsonl")
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"report-{stamp}-{len(places)}.html"
    path.write_text(render(places), encoding="utf-8")
    return path


def list_reports(reports: Path) -> list[dict]:
    if not reports.exists():
        return []
    names = sorted((p.name for p in reports.iterdir() if REPORT_NAME.match(p.name)), reverse=True)
    return [{"name": n, "places": int(n.rsplit("-", 1)[1].removesuffix(".html"))} for n in names]


TEMPLATE = r"""<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Saha Leadleri</title>
<style>__LEAFLET_CSS__</style>
<style>
:root {
  color-scheme: light;
  --surface: #fcfcfb; --card: #ffffff; --line: #e4e3df;
  --ink: #0b0b0b; --ink-2: #52514e; --ink-3: #75746f;
  --series: #2a78d6; --hover: #f1f0ec;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) {
    color-scheme: dark;
    --surface: #1a1a19; --card: #232321; --line: #3a3a37;
    --ink: #f0efec; --ink-2: #c3c2b7; --ink-3: #9b9a92;
    --series: #3987e5; --hover: #2c2c2a;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--surface); color: var(--ink); font: 14px/1.45 system-ui, sans-serif; }
main { max-width: 1200px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 20px; margin: 0 0 4px; }
.sub { color: var(--ink-2); margin: 0 0 20px; }
.filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; margin-bottom: 16px; }
.filters input[type=search], .filters select {
  background: var(--card); color: var(--ink); border: 1px solid var(--line);
  border-radius: 8px; padding: 8px 10px; font: inherit; min-height: 36px;
}
.filters label { color: var(--ink-2); display: flex; gap: 6px; align-items: center; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 16px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
.kpi b { display: block; font-size: 28px; font-weight: 600; font-variant-numeric: tabular-nums; }
.kpi span { color: var(--ink-2); }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 16px; }
@media (max-width: 800px) { .grid { grid-template-columns: 1fr; } }
h2 { font-size: 14px; margin: 0 0 12px; color: var(--ink-2); font-weight: 600; }
.bar { display: grid; grid-template-columns: 150px 1fr 48px; gap: 8px; align-items: center; padding: 3px 0; }
#groupby { background: var(--card); color: var(--ink); border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; font: inherit; min-height: 36px; }
.bar.pick { cursor: pointer; border-radius: 6px; }
.bar.pick:hover, .bar.pick:focus-visible { background: var(--hover); outline: none; }
.bar .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--ink-2); }
.bar .track { height: 10px; }
.bar .fill { height: 10px; background: var(--series); border-radius: 0 4px 4px 0; }
.bar .n { text-align: right; font-variant-numeric: tabular-nums; }
#map { height: 520px; border-radius: 8px; background: var(--hover); }
.pop b { display: block; margin-bottom: 2px; }
.pop div { color: #52514e; }
.tablewrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--ink-2); font-weight: 600; cursor: pointer; white-space: nowrap; user-select: none; }
tr:hover td { background: var(--hover); }
td.num { font-variant-numeric: tabular-nums; }
a { color: var(--series); }
button { background: var(--card); color: var(--ink); border: 1px solid var(--line); border-radius: 8px; padding: 8px 14px; font: inherit; cursor: pointer; min-height: 36px; }
.muted { color: var(--ink-3); }
.tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.tabs button.on { background: var(--series); color: #fff; border-color: var(--series); }
.gap { margin-bottom: 16px; }
.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.row label { color: var(--ink-2); display: flex; gap: 6px; align-items: center; }
.row input[type=url], .row input[type=number], .row select {
  background: var(--card); color: var(--ink); border: 1px solid var(--line);
  border-radius: 8px; padding: 8px 10px; font: inherit; min-height: 36px;
}
.row input[type=url] { flex: 1; min-width: 260px; }
button:disabled { opacity: .5; cursor: not-allowed; }
tr.sel td { background: var(--hover); }
tr.pickrow { cursor: pointer; }
.fields { display: grid; grid-template-columns: 170px 1fr; gap: 4px 12px; margin: 0 0 12px; }
.fields dt { color: var(--ink-2); }
.fields input[type=text], .fields select {
  width: 100%; background: var(--card); color: var(--ink); border: 1px solid var(--line);
  border-radius: 8px; padding: 6px 10px; font: inherit; min-height: 34px;
}
.lbl-actions { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 4px 0 16px; }
button.primary { background: var(--series); color: #fff; border-color: var(--series); }
.fields dd { margin: 0; overflow-wrap: anywhere; }
pre.raw { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px; overflow: auto; max-height: 320px; margin: 0; font-size: 12px; }
.warn { color: #b26a00; }
.job { display: grid; grid-template-columns: 170px 1fr auto; gap: 12px; align-items: center; padding: 6px 0; }
.job .track { height: 10px; background: var(--hover); border-radius: 4px; overflow: hidden; }
.job .fill { height: 10px; background: var(--series); }
.job .state { color: var(--ink-2); font-variant-numeric: tabular-nums; margin: 4px 0 0; }
.err { color: #c0392b; }
.feed { list-style: none; margin: 8px 0 0; padding: 0; color: var(--ink-2); }
.feed li { padding: 2px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
@media (max-width: 800px) { .job { grid-template-columns: 1fr; } }
#tip { position: fixed; pointer-events: none; background: var(--ink); color: var(--surface); padding: 6px 10px; border-radius: 6px; font-size: 12px; display: none; max-width: 260px; z-index: 10; }
</style>
</head>
<body>
<main>
  <h1 id="title">Saha ve tesis leadleri</h1>
  <p class="sub" id="sub"></p>
  <nav class="tabs" id="tabs" hidden>
    <button data-view="data" class="on">Veri</button>
    <button data-view="llm">LLM</button>
  </nav>
  <div id="view-data">
  <section class="card" id="jobs" hidden style="margin-bottom:16px">
    <h2>Veri çekme</h2>
    <div id="jobrows"></div>
    <h2 style="margin-top:12px">Gelen son kayıtlar</h2>
    <ul class="feed" id="feed"></ul>
    <h2 style="margin-top:12px">Kayıtlı raporlar</h2>
    <p><button id="savereport">Raporu şimdi kaydet</button> <span class="muted">Her çekim bittiğinde otomatik kaydedilir (data/reports).</span></p>
    <ul class="feed" id="reports"></ul>
  </section>
  <div class="filters">
    <input type="search" id="q" placeholder="İsim, adres ara" aria-label="Ara">
    <select id="il" aria-label="Şehir"></select>
    <select id="ilce" aria-label="İlçe"></select>
    <select id="type" aria-label="Tür"></select>
    <select id="status" aria-label="Durum"></select>
    <label><input type="checkbox" id="phone"> Telefonu var</label>
    <label><input type="checkbox" id="web"> Web sitesi var</label>
  </div>
  <div class="kpis" id="kpis"></div>
  <div class="grid">
    <section class="card">
      <h2 id="bars-title"></h2>
      <select id="groupby" aria-label="Grupla">
        <option value="type">Türe göre</option>
        <option value="region">Bölgeye göre (şehir, ilçe)</option>
        <option value="query">Keşif sorgusuna göre</option>
      </select>
      <div id="bars" style="margin-top:12px"></div>
    </section>
    <section class="card"><h2>Harita (sürükle, yakınlaştır, noktaya tıkla)</h2><div id="map"></div></section>
  </div>
  <section class="card">
    <h2>Tablo</h2>
    <div class="tablewrap"><table><thead id="thead"></thead><tbody id="tbody"></tbody></table></div>
    <p><button id="more">Daha fazla göster</button> <span class="muted" id="count"></span></p>
  </section>
  </div>

  <div id="view-llm" hidden>
    <section class="card gap">
      <h2>Yerel model</h2>
      <div class="row"><span id="llm-state"></span><select id="llm-model" aria-label="Model"></select></div>
      <p class="muted">Sayfa getirilir (robots.txt'e uyulur), metne çevrilir, model JSON şemasına göre alanları çıkarır. Sayfada geçmeyen telefon, e-posta ve bağlantılar otomatik atılır.</p>
    </section>
    <section class="card gap">
      <h2>Tek sayfayı dene</h2>
      <div class="row"><input type="url" id="llm-url" placeholder="https://ornek-halisaha.com" aria-label="Sayfa adresi"><button id="llm-run-url">Çıkar</button></div>
    </section>
    <section class="card gap">
      <h2>Toplu çalıştır (web sitesi olan yerler)</h2>
      <div class="row">
        <label>Kaç yer <input type="number" id="llm-limit" value="5" min="1" max="200" style="width:80px"></label>
        <label><input type="checkbox" id="llm-visible"> Yalnızca Veri sekmesinde görünenler</label>
        <button id="llm-run-places">Başlat</button>
      </div>
      <p class="muted" id="llm-coverage"></p>
      <div id="llm-job"></div>
    </section>
    <section class="card gap">
      <h2>Çıktılar (en yeni üstte; satıra tıkla)</h2>
      <div class="row"><label><input type="checkbox" id="llm-unlabeled"> Yalnızca etiketsiz</label></div>
      <div id="llm-acc" class="muted" style="margin:8px 0"></div>
      <div class="tablewrap"><table>
        <thead><tr><th>Zaman</th><th>Yer / adres</th><th>Tür</th><th>Spor yeri</th><th>Telefon</th><th>Rezervasyon</th><th>İnceleme</th><th>Etiket</th><th>Süre</th><th>Durum</th></tr></thead>
        <tbody id="llm-rows"></tbody>
      </table></div>
      <p class="muted" id="llm-empty"></p>
    </section>
    <section class="card"><h2>Seçili çıktı</h2><div id="llm-detail"></div></section>
  </div>
</main>
<div id="tip" role="tooltip"></div>
<script>__LEAFLET_JS__</script>
<script id="data" type="application/json">__DATA__</script>
<script>
const LIVE = __LIVE__;
// "Demetevler, Yeşilay Cd., 06200 Yenimahalle/Ankara" -> ilce "Yenimahalle", il "Ankara"
function enrich(list) {
  list.forEach((p) => {
    const m = /([^,\/\d]+)\/([^,\/]+?)(?:,\s*Türkiye)?\s*$/.exec(p.address || '');
    p.ilce = m ? m[1].trim() : '';
    p.il = m ? m[2].trim() : '';
  });
  return list;
}
let D = enrich(JSON.parse(document.getElementById('data').textContent));
const state = { group: 'type', q: '', il: '', ilce: '', type: '', status: '', phone: false, web: false, sort: 'name', dir: 1, limit: 100 };
const $ = (s) => document.querySelector(s);
const el = (tag, props = {}, ...kids) => { const e = document.createElement(tag); Object.assign(e, props); e.append(...kids); return e; };
const hasDetail = () => D.some((p) => p.detail);
const norm = (s) => (s || '').toLocaleLowerCase('tr');
const count = (items) => { const m = new Map(); items.forEach((k) => m.set(k, (m.get(k) || 0) + 1)); return [...m].sort((a, b) => b[1] - a[1]); };
const NO_TYPE = '(tür yok)';

function filtered() {
  const q = norm(state.q);
  return D.filter((p) =>
    (!q || norm(p.name).includes(q) || norm(p.address).includes(q)) &&
    (!state.il || (p.il || NO_TYPE) === state.il) &&
    (!state.ilce || (p.ilce || NO_TYPE) === state.ilce) &&
    (!state.type || (p.type || NO_TYPE) === state.type) &&
    (!state.status || (p.status || NO_TYPE) === state.status) &&
    (!state.phone || p.phone) && (!state.web || p.web));
}

function fillSelect(sel, label, values) {
  const keep = sel.value;
  sel.replaceChildren(el('option', { value: '', textContent: label }),
    ...values.map(([v, n]) => el('option', { value: v, textContent: v + ' (' + n + ')' })));
  sel.value = keep;
}
function fillFilters() {
  fillSelect($('#il'), 'Tüm şehirler', count(D.map((p) => p.il || NO_TYPE)));
  fillSelect($('#ilce'), 'Tüm ilçeler', count(D.filter((p) => !state.il || (p.il || NO_TYPE) === state.il).map((p) => p.ilce || NO_TYPE)));
  fillSelect($('#type'), 'Tüm türler', count(D.map((p) => p.type || NO_TYPE)));
  fillSelect($('#status'), 'Tüm durumlar', count(D.map((p) => p.status || NO_TYPE)));
}

const distinct = (list, key) => new Set(list.map((p) => p[key]).filter(Boolean)).size;
function kpis(list) {
  const stats = [
    ['Yer', list.length],
    ['Şehir / ilçe', distinct(list, 'il') + ' / ' + distinct(list, 'ilce')],
    ['Detayı çekilen', list.filter((p) => p.detail).length],
    ['Telefonlu', list.filter((p) => p.phone).length],
    ['Web siteli', list.filter((p) => p.web).length],
    ['Faal', list.filter((p) => p.status === 'OPERATIONAL').length],
  ];
  $('#kpis').replaceChildren(...stats.map(([label, n]) =>
    el('div', { className: 'card kpi' }, el('b', { textContent: typeof n === 'number' ? n.toLocaleString('tr') : n }), el('span', { textContent: label }))));
}

function setRegion(il, ilce) {
  state.il = il; state.ilce = ilce; state.limit = 100;
  fillFilters();
  $('#il').value = il; $('#ilce').value = ilce;
  render();
}

function bars(list) {
  // Without details there are no types or regions yet, so fall back to the discovery queries.
  const group = hasDetail() ? state.group : 'query';
  const byRegion = group === 'region';
  let rows, title, onPick = null;
  if (byRegion && !state.il) {
    rows = count(list.map((p) => p.il || NO_TYPE)); title = 'Şehre göre yer sayısı (tıkla: şehre in)';
    onPick = (name) => setRegion(name, '');
  } else if (byRegion && !state.ilce) {
    rows = count(list.map((p) => p.ilce || NO_TYPE)); title = state.il + ' ilçelerine göre yer sayısı (tıkla: ilçeye in)';
    onPick = (name) => setRegion(state.il, name);
  } else if (byRegion) {
    rows = count(list.map((p) => p.type || NO_TYPE)); title = state.il + ' / ' + state.ilce + ': türe göre yer sayısı';
  } else if (group === 'type') {
    rows = count(list.map((p) => p.type || NO_TYPE)); title = 'Türe göre yer sayısı';
  } else {
    rows = count(list.flatMap((p) => p.queries)); title = 'Sorguya göre bulunan yer sayısı' + (hasDetail() ? '' : ' (detay henüz çekilmedi)');
  }
  $('#bars-title').textContent = title;
  $('#groupby').value = state.group;
  rows = rows.slice(0, 15);
  const max = rows.length ? rows[0][1] : 1;
  $('#bars').replaceChildren(...rows.map(([name, n]) => {
    const row = el('div', { className: 'bar' },
      el('span', { className: 'name', textContent: name }),
      el('div', { className: 'track' }, el('div', { className: 'fill', style: 'width:' + (n / max * 100) + '%' })),
      el('span', { className: 'n', textContent: n }));
    row.dataset.tip = name + ': ' + n;
    if (onPick && name !== NO_TYPE) {
      row.classList.add('pick'); row.tabIndex = 0; row.setAttribute('role', 'button');
      row.onclick = () => onPick(name);
      row.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(name); } };
    }
    return row;
  }));
}


let lmap = null, markers = null, lastMapSig = '', lastFitKey = '', fitted = false;
function ensureMap() {
  if (lmap || typeof L === 'undefined') return;
  lmap = L.map('map', { preferCanvas: true }).setView([39, 35], 6);  // Turkey; fitBounds takes over as soon as there are points
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors',
  }).addTo(lmap);
  markers = L.layerGroup().addTo(lmap);
}
function popup(p) {
  const web = /^https?:/i.test(p.web || '') ? el('a', { href: p.web, textContent: new URL(p.web).hostname, target: '_blank', rel: 'noopener noreferrer' }) : '';
  const maps = p.url ? el('a', { href: p.url, textContent: 'Google Maps', target: '_blank', rel: 'noopener noreferrer' }) : '';
  return el('div', { className: 'pop' }, el('b', { textContent: p.name || p.id }),
    ...[p.type, p.address, p.phone].filter(Boolean).map((t) => el('div', { textContent: t })),
    web, web && maps ? ' · ' : '', maps);
}
function drawMap(list) {
  ensureMap();
  if (!lmap) return;
  const pts = list.filter((p) => p.lat != null && p.lng != null);
  const filterKey = [state.q, state.il, state.ilce, state.type, state.status, state.phone, state.web].join('|');
  const sig = filterKey + ':' + pts.length;
  if (sig === lastMapSig) return;  // polling must not rebuild markers or move the view the user is panning
  lastMapSig = sig;
  const color = getComputedStyle(document.documentElement).getPropertyValue('--series').trim() || '#2a78d6';
  markers.clearLayers();
  pts.forEach((p) => {
    const m = L.circleMarker([p.lat, p.lng], { radius: 6, weight: 1.5, color: '#fff', fillColor: color, fillOpacity: 0.85 });
    m.bindTooltip(el('span', { textContent: p.name || p.id }));
    m.bindPopup(popup(p));
    markers.addLayer(m);
  });
  if (pts.length && (filterKey !== lastFitKey || !fitted)) {
    lmap.invalidateSize();
    lmap.fitBounds(L.latLngBounds(pts.map((p) => [p.lat, p.lng])), { padding: [24, 24], maxZoom: 16 });
    lastFitKey = filterKey; fitted = true;
  }
}

const COLS = [['name', 'İsim'], ['ilce', 'Bölge'], ['type', 'Tür'], ['status', 'Durum'], ['phone', 'Telefon'], ['web', 'Web'], ['rating', 'Puan'], ['ratings', 'Yorum']];
function table(list) {
  $('#thead').replaceChildren(el('tr', {}, ...COLS.map(([k, label]) => {
    const th = el('th', { textContent: label + (state.sort === k ? (state.dir > 0 ? ' ▲' : ' ▼') : '') });
    th.onclick = () => { state.dir = state.sort === k ? -state.dir : 1; state.sort = k; render(); };
    return th;
  })));
  const s = state.sort;
  const sorted = [...list].sort((a, b) => {
    const x = a[s] ?? '', y = b[s] ?? '';
    if (x === '' && y !== '') return 1;
    if (y === '' && x !== '') return -1;
    return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'tr')) * state.dir;
  });
  const link = (href, text) => href ? el('a', { href, textContent: text, target: '_blank', rel: 'noopener noreferrer' }) : '';
  $('#tbody').replaceChildren(...sorted.slice(0, state.limit).map((p) => el('tr', {},
    el('td', {}, p.url ? link(p.url, p.name || p.id) : (p.name || p.id), p.address ? el('div', { className: 'muted', textContent: p.address }) : ''),
    el('td', { textContent: [p.ilce, p.il].filter(Boolean).join(', ') }),
    el('td', { textContent: p.type || '' }),
    el('td', { textContent: p.status || '' }),
    el('td', { textContent: p.phone || '' }),
    el('td', {}, /^https?:/i.test(p.web || '') ? link(p.web, new URL(p.web).hostname) : ''),
    el('td', { className: 'num', textContent: p.rating ?? '' }),
    el('td', { className: 'num', textContent: p.ratings ?? '' }))));
  $('#count').textContent = Math.min(state.limit, sorted.length) + ' / ' + sorted.length;
  $('#more').style.display = sorted.length > state.limit ? '' : 'none';
}

function render() {
  $('#sub').textContent = LIVE ? 'Canlı: yerel sunucudan okunuyor, ilerleme otomatik yenilenir.'
    : hasDetail() ? 'Google Places verisi, yerel dosyadan üretildi.' : 'Yalnızca place ID listesi var; detaylar çekilince tablo dolar.';
  $('#title').textContent = state.il ? state.il + (state.ilce ? ' / ' + state.ilce : '') + ' saha ve tesis leadleri' : 'Saha ve tesis leadleri';
  const list = filtered(); visibleIds = list.map((p) => p.id); kpis(list); bars(list); drawMap(list); table(list); renderJobs();
}

let progress = null, lastSig = '', visibleIds = [];
const fmt = (n) => n.toLocaleString('tr');
async function post(body) {
  const r = await fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Leadgen': '1' }, body: JSON.stringify(body) });
  if (!r.ok) alert((await r.json().catch(() => ({}))).error || 'Başlatılamadı');
  poll();
}
function jobRow(name, s, button) {
  const pct = s.total ? Math.round(s.done / s.total * 100) : 0;
  const state = s.total ? fmt(s.done) + ' / ' + fmt(s.total) + ' (%' + pct + ')' + (s.running ? ' · çalışıyor' : '') : 'henüz başlatılmadı';
  const mid = el('div', {}, el('div', { className: 'track' }, el('div', { className: 'fill', style: 'width:' + pct + '%' })),
    el('p', { className: 'state' + (s.error ? ' err' : ''), textContent: s.error ? 'Hata: ' + s.error : state }));
  return el('div', { className: 'job' }, el('span', { textContent: name }), mid, button || '');
}
function renderJobs() {
  const box = $('#jobs');
  if (!LIVE || !progress) return;
  box.hidden = false;
  const p = progress;
  const proBtn = !p.pro.running && p.pro.done < p.pro.total ? el('button', { textContent: 'Pro detaylarını çek', onclick: () => post({ tier: 'pro' }) }) : '';
  const n = visibleIds.length;
  const cost = Math.max(0, n - 1000) * 20 / 1000;
  const entBtn = !p.enterprise.running && n && n < D.length ? el('button', {
    textContent: 'Görünen ' + n + ' yerin telefon/web bilgisini çek',
    onclick: () => { if (confirm(n + ' yer için Enterprise çağrısı yapılacak. Ayda 1.000 çağrı ücretsiz; bu çalıştırmanın tahmini ücreti en çok $' + cost.toFixed(2) + '. Devam?')) post({ tier: 'enterprise', ids: visibleIds }); },
  }) : '';
  $('#jobrows').replaceChildren(
    jobRow('Keşif (place ID)', { done: p.ids, total: p.ids, running: false }),
    jobRow('Pro: isim, tür, konum', p.pro, proBtn),
    jobRow('Enterprise: telefon, web', p.enterprise, entBtn));
  $('#feed').replaceChildren(...p.recent.map((r) => el('li', { textContent: [r.name, r.type, r.status, r.phone, r.web].filter(Boolean).join(' · ') })));
}
let reportSig = '';
async function loadReports() {
  const reports = await (await fetch('/api/reports')).json();
  const sig = reports.map((r) => r.name).join();
  if (sig === reportSig) return;
  reportSig = sig;
  $('#reports').replaceChildren(...reports.map((r) => el('li', {},
    el('a', { href: '/reports/' + r.name, target: '_blank', rel: 'noopener', textContent: r.name.replace(/^report-|\.html$/g, '') + ' · ' + fmt(r.places) + ' yer' }))));
}
$('#savereport').onclick = async () => {
  await fetch('/api/save-report', { method: 'POST', headers: { 'X-Leadgen': '1' } });
  loadReports();
};
async function poll() {
  try {
    progress = await (await fetch('/api/progress')).json();
    const sig = progress.pro.done + ':' + progress.enterprise.done;
    if (sig !== lastSig) { lastSig = sig; D = enrich(await (await fetch('/api/places')).json()); fillFilters(); }
    render();
    loadReports();
  } catch (e) { $('#sub').textContent = 'Sunucuya ulaşılamıyor (leadgen serve çalışıyor mu?)'; }
}
if (LIVE) { setInterval(poll, 2000); poll(); }

// ---- LLM page ----
const llm = { status: null, results: [], labels: {}, accuracy: null, selected: '', sig: '', model: '', detailKey: '' };
const BOOKING = { yes: 'var', no: 'yok', unknown: 'belirsiz' };
const KIND = { halisaha: 'halı saha', spor_kompleksi: 'spor kompleksi', spor_salonu: 'spor salonu', stadyum: 'stadyum', okul_veya_kurum: 'okul/kurum', diger: 'diğer' };
const PAGE_KIND = { venue_site: 'tesisin kendi sitesi', social_profile: 'tesisin sosyal profili', directory_or_news: 'haber/rehber/dizin', other: 'diğer' };
const recKey = (r) => r.ts + '|' + r.url;
const host = (u) => { try { return new URL(u).hostname; } catch (e) { return u; } };

function showView(view) {
  $('#view-data').hidden = view !== 'data';
  $('#view-llm').hidden = view !== 'llm';
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.view === view));
  history.replaceState(null, '', view === 'llm' ? '#llm' : location.pathname);
  if (view === 'llm') pollLlm(true);
  else if (lmap) { lmap.invalidateSize(); lastMapSig = ''; fitted = false; render(); }  // the map was sized while hidden
}
async function postJson(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Leadgen': '1' }, body: JSON.stringify(body) });
  if (!r.ok) alert((await r.json().catch(() => ({}))).error || 'Başlatılamadı');
  pollLlm(true);
}

function factRows(r) {
  const f = r.facts;
  const list = (a) => (a && a.length ? a.join(', ') : '-');
  return [
    ['Sayfa türü', PAGE_KIND[f.page_kind] || f.page_kind], ['Spor yeri mi', f.is_sports_venue ? 'evet' : 'hayır'], ['Tür', KIND[f.venue_type] || f.venue_type],
    ['Sporlar', list(f.sports)], ['Telefon', list(f.phones)], ['E-posta', list(f.emails)],
    ['WhatsApp', f.whatsapp || '-'], ['Instagram', f.instagram || '-'],
    ['Online rezervasyon', BOOKING[f.online_booking] || f.online_booking], ['Rezervasyon bağlantısı', f.booking_url || '-'],
    ['Dayanak (sayfadan alıntı)', f.evidence || '-'],
  ];
}
const FIELD_LABEL = { page_kind: 'sayfa türü', is_sports_venue: 'spor yeri', venue_type: 'tür', sports: 'sporlar', phones: 'telefon', emails: 'e-posta', whatsapp: 'whatsapp', instagram: 'instagram', online_booking: 'rezervasyon' };
const OPTS = {
  page_kind: Object.entries(PAGE_KIND), venue_type: Object.entries(KIND), online_booking: Object.entries(BOOKING),
  is_sports_venue: [['true', 'evet'], ['false', 'hayır']],
};
async function saveLabel(key, status, gold, note) {
  const r = await fetch('/api/llm/label', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Leadgen': '1' }, body: JSON.stringify({ key, status, gold, note }) });
  if (!r.ok) { alert((await r.json().catch(() => ({}))).error || 'Kaydedilemedi'); return; }
  await pollLlm(true);
  const next = llm.results.find((x) => x.facts && !llm.labels[recKey(x)] && recKey(x) !== key);
  if (next) llm.selected = recKey(next);  // keep the flow going: jump to the next unlabeled record
  llm.detailKey = '';
  renderLlm();
}
function labelForm(r) {
  const key = recKey(r), cur = llm.labels[key], src = (cur && cur.gold) || r.facts;
  const sel = (name, value) => {
    const s = el('select', { name });
    OPTS[name].forEach(([v, t]) => s.append(el('option', { value: v, textContent: t })));
    s.value = String(value);
    return s;
  };
  const txt = (value) => el('input', { type: 'text', value: Array.isArray(value) ? value.join(', ') : (value || '') });
  const list = (v) => v.split(',').map((x) => x.trim()).filter(Boolean);
  const i = {
    page_kind: sel('page_kind', src.page_kind), is_sports_venue: sel('is_sports_venue', src.is_sports_venue), venue_type: sel('venue_type', src.venue_type),
    sports: txt(src.sports), phones: txt(src.phones), emails: txt(src.emails), whatsapp: txt(src.whatsapp), instagram: txt(src.instagram),
    online_booking: sel('online_booking', src.online_booking),
  };
  const note = txt(cur ? cur.note : '');
  const collect = () => ({
    page_kind: i.page_kind.value, is_sports_venue: i.is_sports_venue.value === 'true', venue_type: i.venue_type.value,
    sports: list(i.sports.value), phones: list(i.phones.value), emails: list(i.emails.value),
    whatsapp: i.whatsapp.value.trim() || null, instagram: i.instagram.value.trim() || null, online_booking: i.online_booking.value,
  });
  const google = D.find((p) => p.id === r.place_id);
  const status = cur ? (cur.status === 'unsure' ? 'Etiket: emin değilim' : 'Etiket: ' + (cur.corrected.length ? 'düzeltildi (' + cur.corrected.map((f) => FIELD_LABEL[f]).join(', ') + ')' : 'onaylandı')) : 'Etiketsiz';
  const hints = [];
  if (google && google.phone) hints.push("Google'daki telefon: " + google.phone);
  if ((r.flags || []).some((f) => f.startsWith('çelişki'))) hints.push('Çelişkili kayıt: sayfa spam ya da ele geçirilmiş olabilir, açarken dikkat et.');
  return el('div', {},
    el('h2', { textContent: 'Etiketle: değerleri sayfaya bakarak düzelt (virgülle ayır)' }),
    el('p', { className: 'muted', textContent: [status, ...hints].join(' · ') }),
    el('dl', { className: 'fields' }, ...Object.entries(i).flatMap(([k, input]) => [el('dt', { textContent: FIELD_LABEL[k] }), el('dd', {}, input)]),
      el('dt', { textContent: 'not' }), el('dd', {}, note)),
    el('div', { className: 'lbl-actions' },
      el('button', { className: 'primary', textContent: 'Kaydet', onclick: () => saveLabel(key, 'labeled', collect(), note.value) }),
      el('button', { textContent: 'Emin değilim', onclick: () => saveLabel(key, 'unsure', null, note.value) }),
      el('a', { href: r.url, target: '_blank', rel: 'noopener noreferrer', textContent: 'Sayfayı aç' })));
}

function detail(r) {
  const box = $('#llm-detail');
  if (!r) { box.replaceChildren(el('p', { className: 'muted', textContent: 'Henüz çıktı yok.' })); return; }
  const meta = [r.name, host(r.url), r.model, r.seconds + ' sn', r.chars + ' karakter', new Date(r.ts).toLocaleString('tr')].filter(Boolean).join(' · ');
  const parts = [el('p', { className: 'muted', textContent: meta })];
  if (r.error) parts.push(el('p', { className: 'err', textContent: 'Hata: ' + r.error }));
  if ((r.flags || []).length) parts.push(el('p', { className: 'warn', textContent: 'İnceleme gerekli: ' + r.flags.join('; ') }));
  if (r.facts) {
    parts.push(el('dl', { className: 'fields' }, ...factRows(r).flatMap(([k, v]) => [el('dt', { textContent: k }), el('dd', { textContent: v })])));
    if (r.dropped.length) {
      parts.push(el('p', { className: 'warn', textContent: 'Atılan değerler: ' + r.dropped.map((d) => d.field + ' = ' + d.value + ' (' + (d.reason || 'sayfada yok') + ')').join('; ') }));
    }
    parts.push(labelForm(r));
    parts.push(el('pre', { className: 'raw', textContent: JSON.stringify(r.facts, null, 2) }));
  }
  box.replaceChildren(...parts);
}

function renderLlm() {
  const s = llm.status;
  if (!s) return;
  const running = s.job.running;
  $('#llm-state').textContent = s.ollama.up ? 'Ollama çalışıyor' : 'Ollama kapalı (terminalde: ollama serve)';
  $('#llm-state').className = s.ollama.up ? '' : 'err';
  const sel = $('#llm-model');
  if (sel.options.length !== s.ollama.models.length) {
    if (!llm.model) llm.model = s.ollama.models.includes(s.default_model) ? s.default_model : (s.ollama.models[0] || '');
    sel.replaceChildren(...s.ollama.models.map((m) => el('option', { value: m, textContent: m })));
    sel.value = llm.model;
  }
  $('#llm-coverage').textContent = s.with_website
    ? s.with_website + ' yerin web sitesi var, ' + s.extracted + ' tanesi işlendi.'
    : 'Henüz web sitesi olan yer yok: telefon/web (Enterprise) çekimi bittikten sonra burası dolar. Şimdilik tek sayfa denemesini kullanabilirsin.';
  for (const id of ['#llm-run-url', '#llm-run-places']) $(id).disabled = running || !s.ollama.up;
  $('#llm-job').replaceChildren(s.job.total
    ? jobRow(running ? 'İşleniyor' : 'Son iş', { done: s.job.done, total: s.job.total, running, error: null }, '')
    : '');
  if (running && s.job.current) $('#llm-job').append(el('p', { className: 'muted', textContent: host(s.job.current) }));

  if (!llm.selected && llm.results.length) llm.selected = recKey(llm.results[0]);
  $('#llm-empty').textContent = llm.results.length ? '' : 'Henüz çıktı yok. Yukarıdan bir sayfa dene.';
  const onlyNew = $('#llm-unlabeled').checked;
  const shown = llm.results.filter((r) => !onlyNew || (r.facts && !llm.labels[recKey(r)]));
  $('#llm-rows').replaceChildren(...shown.map((r) => {
    const f = r.facts;
    const tr = el('tr', { className: 'pickrow' + (recKey(r) === llm.selected ? ' sel' : ''), tabIndex: 0 },
      el('td', { textContent: new Date(r.ts).toLocaleString('tr', { dateStyle: 'short', timeStyle: 'short' }) }),
      el('td', { textContent: r.name || host(r.url) }),
      el('td', { textContent: f ? (KIND[f.venue_type] || f.venue_type) : '' }),
      el('td', { textContent: f ? (f.is_sports_venue ? 'evet' : 'hayır') : '' }),
      el('td', { textContent: f ? f.phones.join(', ') : '' }),
      el('td', { textContent: f ? (BOOKING[f.online_booking] || '') : '' }),
      el('td', { className: (r.flags || []).length ? 'warn' : '', textContent: f ? ((r.flags || []).length ? (r.flags.length + ' uyarı') : '-') : '' }),
      el('td', { textContent: (() => { const l = llm.labels[recKey(r)]; return l ? (l.status === 'unsure' ? '?' : (l.corrected.length ? 'düzeltildi' : 'onaylı')) : (f ? '-' : ''); })() }),
      el('td', { className: 'num', textContent: r.seconds + ' sn' }),
      el('td', { className: r.ok ? '' : 'err', textContent: r.ok ? 'tamam' + (r.dropped.length ? ' · ' + r.dropped.length + ' atıldı' : '') : r.error }));
    const pick = () => { llm.selected = recKey(r); llm.sig = ''; llm.detailKey = ''; renderLlm(); };
    tr.onclick = pick;
    tr.onkeydown = (e) => { if (e.key === 'Enter') pick(); };
    return tr;
  }));
  // The form is rebuilt only when the selection or its label changes, so polling never wipes what is being typed.
  const lbl = llm.labels[llm.selected];
  const detailKey = llm.selected + '|' + (lbl ? lbl.ts : '');
  if (detailKey !== llm.detailKey) { llm.detailKey = detailKey; detail(llm.results.find((r) => recKey(r) === llm.selected)); }
  const a = llm.accuracy;
  $('#llm-acc').textContent = a && (a.labeled || a.unsure)
    ? a.labeled + ' etiketli, ' + a.unsure + ' emin değil. Model = etiket: ' + Object.entries(a.fields).map(([f, [ok, n]]) => FIELD_LABEL[f] + ' ' + ok + '/' + n).join(' · ') + (a.labeled < 30 ? ' (örnek az, oran yorumlanmaz)' : '')
    : 'Henüz etiket yok.';
}

async function pollLlm(force) {
  if ($('#view-llm').hidden) return;
  try {
    const [status, results, labels] = await Promise.all(['status', 'results', 'labels'].map((n) => fetch('/api/llm/' + n).then((r) => r.json())));
    const sig = JSON.stringify([status, results.length, results[0] && results[0].ts, labels]);
    llm.status = status; llm.results = results; llm.labels = labels.labels; llm.accuracy = labels.accuracy;
    if (force || sig !== llm.sig) { llm.sig = sig; renderLlm(); }
  } catch (e) { $('#llm-state').textContent = 'Sunucuya ulaşılamıyor'; }
}
$('#llm-model').onchange = (e) => { llm.model = e.target.value; };
$('#llm-unlabeled').onchange = () => { llm.sig = ''; renderLlm(); };
$('#llm-run-url').onclick = () => {
  const url = $('#llm-url').value.trim();
  if (url) postJson('/api/llm/run', { mode: 'url', url, model: llm.model || undefined });
};
$('#llm-run-places').onclick = () => postJson('/api/llm/run', {
  mode: 'places', limit: Number($('#llm-limit').value) || 5, model: llm.model || undefined,
  ids: $('#llm-visible').checked ? visibleIds : undefined,
});
document.querySelectorAll('#tabs button').forEach((b) => { b.onclick = () => showView(b.dataset.view); });
if (LIVE) {
  $('#tabs').hidden = false;
  setInterval(pollLlm, 2000);
  if (location.hash === '#llm') showView('llm');
}

fillFilters();
$('#q').oninput = (e) => { state.q = e.target.value; state.limit = 100; render(); };
$('#il').onchange = (e) => { state.il = e.target.value; state.ilce = ''; $('#ilce').value = ''; state.limit = 100; fillFilters(); render(); };
$('#groupby').onchange = (e) => { state.group = e.target.value; render(); };
$('#ilce').onchange = (e) => { state.ilce = e.target.value; state.limit = 100; render(); };
$('#type').onchange = (e) => { state.type = e.target.value; state.limit = 100; render(); };
$('#status').onchange = (e) => { state.status = e.target.value; state.limit = 100; render(); };
$('#phone').onchange = (e) => { state.phone = e.target.checked; render(); };
$('#web').onchange = (e) => { state.web = e.target.checked; render(); };
$('#more').onclick = () => { state.limit += 200; render(); };
const tip = $('#tip');
document.addEventListener('mouseover', (e) => {
  const t = e.target.closest('[data-tip]');
  if (!t) { tip.style.display = 'none'; return; }
  tip.textContent = t.dataset.tip; tip.style.display = 'block';
});
document.addEventListener('mousemove', (e) => { tip.style.left = (e.clientX + 12) + 'px'; tip.style.top = (e.clientY + 12) + 'px'; });
render();
</script>
</body>
</html>
"""

"""Venue web page -> structured facts, using a local Ollama model.

Pipeline: fetch (robots.txt + SSRF guard) -> visible text and contact links -> model with a JSON schema
-> grounding (values the page does not contain are dropped, so a hallucinated phone never reaches a lead).
"""

import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Literal
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ValidationError

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:9b"
USER_AGENT = "Mozilla/5.0 (compatible; leadgen-research/0.1)"
HEAD_CHARS, TAIL_CHARS = 7000, 5000  # contact details usually sit in the footer, so keep both ends
MAX_REDIRECTS = 3
MAX_BYTES = 2_000_000
OWN_PAGE_KINDS = ("venue_site", "social_profile")

SYSTEM_PROMPT = (
    "Sen bir spor tesisi web sayfasından bilgi çıkaran asistansın. Yalnızca verilen sayfa metnindeki "
    "bilgileri kullan. Metinde açıkça yazmayan hiçbir değeri uydurma: bulamazsan null, boş liste ya da "
    "'unknown' yaz. Telefon, e-posta ve bağlantıları metinde göründüğü gibi aynen kopyala. "
    "Önce page_kind'a karar ver: sayfa tesisin kendi sitesiyse 'venue_site', tesisin kendi sosyal medya "
    "profiliyse 'social_profile', haber/rehber/dizin/harita/yorum sitesi gibi tesis dışı bir sayfaysa "
    "'directory_or_news'. Tesis dışı sayfada sitenin kendi iletişim bilgilerini (haber sitesinin e-postası, "
    "WhatsApp hattı, sosyal medyası) tesise ait gösterme. "
    "evidence: is_sports_venue kararını destekleyen, sayfa metninden AYNEN kopyalanmış en fazla 200 karakterlik alıntı; "
    "metinde böyle bir yer yoksa boş bırak. "
    "online_booking: sayfada online rezervasyon/randevu sistemi veya formu varsa 'yes', yalnızca "
    "telefonla rezervasyon yazıyorsa 'no', belli değilse 'unknown'."
)


class VenueFacts(BaseModel):
    page_kind: Literal["venue_site", "social_profile", "directory_or_news", "other"]
    is_sports_venue: bool
    venue_type: Literal["halisaha", "spor_kompleksi", "spor_salonu", "stadyum", "okul_veya_kurum", "diger"]
    sports: list[str]
    phones: list[str]
    emails: list[str]
    whatsapp: str | None
    instagram: str | None
    online_booking: Literal["yes", "no", "unknown"]
    booking_url: str | None
    evidence: str


class FetchError(RuntimeError):
    pass


@dataclass
class Page:
    url: str
    text: str


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template"}
    LINK_HOSTS = ("wa.me", "whatsapp.com", "instagram.com", "facebook.com")

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.skip = 0
        self.in_title = False
        self.title = ""
        self.chunks: list[str] = []
        self.links: list[str] = []
        self._href: str | None = None
        self._label: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._label = []
        elif tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag == "title":
            self.in_title = False
        elif tag == "a" and self._href:
            href = self._href.strip()
            label = " ".join("".join(self._label).split())
            interesting = href.startswith(("tel:", "mailto:")) or any(h in href for h in self.LINK_HOSTS) or (
                re.search(r"rezerv|randevu|booking", href + " " + label, re.I) is not None
            )
            if interesting:
                self.links.append(f"{label or '-'} -> {urljoin(self.base, href)}")
            self._href = None

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        if not self.skip:
            self.chunks.append(data)
            self._label.append(data)


def html_to_text(html: str, base_url: str) -> str:
    parser = _TextExtractor(base_url)
    parser.feed(html)
    body = re.sub(r"[ \t]+", " ", "".join(parser.chunks))
    body = re.sub(r"\n\s*\n+", "\n", body).strip()
    if len(body) > HEAD_CHARS + TAIL_CHARS:
        body = body[:HEAD_CHARS] + "\n[...]\n" + body[-TAIL_CHARS:]
    links = "\n".join(dict.fromkeys(parser.links))
    return f"Başlık: {parser.title.strip()}\n\n{body}\n\nBağlantılar:\n{links}".strip()


def _assert_public(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError(f"{host} resolves to a non-public address")


def fetch_page(url: str, client: httpx.Client) -> Page:
    """GET a public http(s) page; refuses private addresses (also on redirects) and respects robots.txt."""
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise FetchError("only http(s) URLs are allowed")
        _assert_public(parts.hostname)
        robots = robotparser.RobotFileParser()
        try:
            resp = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt", headers={"User-Agent": USER_AGENT})
            robots.parse(resp.text.splitlines() if resp.status_code == 200 else [])
        except httpx.HTTPError:
            robots.parse([])
        if not robots.can_fetch(USER_AGENT, url):
            raise FetchError("blocked by robots.txt")
        resp = client.get(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "tr,en;q=0.8"})
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
            url = urljoin(url, resp.headers["location"])
            continue
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code}")
        if "html" not in resp.headers.get("content-type", "html").lower():
            raise FetchError("not an HTML page")
        return Page(url, html_to_text(resp.text[:MAX_BYTES], url))
    raise FetchError("too many redirects")


def _phone_cores(text: str) -> set[str]:
    cores = set()
    for run in re.findall(r"\d[\d\s\-().+]{7,}\d", text):
        digits = re.sub(r"\D", "", run)
        if len(digits) >= 10:
            cores.add(digits[-10:])
    return cores


def ground(facts: dict, page_text: str) -> tuple[dict, list[dict]]:
    """Drop contact values that do not literally appear in the page; return (clean facts, dropped)."""
    lower = page_text.lower()
    cores = _phone_cores(page_text)
    clean, dropped = dict(facts), []
    # Contacts on a news/directory page belong to that site, not to the venue it writes about.
    foreign = facts["page_kind"] not in OWN_PAGE_KINDS
    reason = f"page_kind={facts['page_kind']}" if foreign else "sayfada yok"

    def check(field: str, value: str | None, ok: bool) -> bool:
        ok = ok and not foreign
        if value and not ok:
            dropped.append({"field": field, "value": value, "reason": reason})
        return ok

    clean["phones"] = [p for p in facts["phones"] if check("phones", p, re.sub(r"\D", "", p)[-10:] in cores)]
    clean["emails"] = [e for e in facts["emails"] if check("emails", e, e.lower() in lower)]
    wa = facts["whatsapp"]
    wa_ok = bool(wa) and (re.sub(r"\D", "", wa)[-10:] in cores or wa.lower() in lower)
    clean["whatsapp"] = wa if check("whatsapp", wa, wa_ok) else None
    ig = facts["instagram"]
    handle = ig.rstrip("/").rsplit("/", 1)[-1].lstrip("@").lower() if ig else ""
    clean["instagram"] = ig if check("instagram", ig, bool(handle) and handle in lower) else None
    bu = facts["booking_url"]
    clean["booking_url"] = bu if check("booking_url", bu, bool(bu) and bu.lower().rstrip("/") in lower) else None
    return clean, dropped


PLACEHOLDER_SPORTS = {"", "bilinmiyor", "unknown", "belirtilmemiş", "yok", "-", "null"}


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def review_flags(facts: dict, page_text: str) -> list[str]:
    """Reasons a human should look at this record; empty means no rule fired (not that it is correct)."""
    flags = []
    if not facts["is_sports_venue"] and (facts["online_booking"] == "yes" or facts["venue_type"] != "diger"):
        flags.append("çelişki: spor yeri değil ama tür/rezervasyon dolu")
    if facts["is_sports_venue"] and not facts["sports"]:
        flags.append("spor yeri ama hangi spor belli değil")
    if facts["page_kind"] not in OWN_PAGE_KINDS:
        flags.append("tesis dışı sayfa: iletişim bilgisi atıldı")
    evidence = _norm(facts["evidence"])
    if not evidence or evidence not in _norm(page_text):
        flags.append("dayanak sayfada bulunamadı")
    return flags


def ask_model(client: httpx.Client, model: str, page: Page, ollama_url: str = OLLAMA_URL) -> VenueFacts:
    resp = client.post(
        f"{ollama_url}/api/chat",
        timeout=600,
        json={
            "model": model,
            "stream": False,
            "think": False,
            "keep_alive": "10m",
            "format": VenueFacts.model_json_schema(),
            "options": {"temperature": 0, "num_ctx": 6144},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Sayfa: {page.url}\n\n{page.text}"},
            ],
        },
    )
    resp.raise_for_status()
    return VenueFacts.model_validate_json(resp.json()["message"]["content"])


def run_extraction(
    url: str,
    model: str,
    http: httpx.Client,
    place_id: str | None = None,
    name: str | None = None,
    ollama_url: str = OLLAMA_URL,
) -> dict:
    """One extraction record; failures are recorded, not raised, so a batch keeps going."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "place_id": place_id, "name": name, "url": url, "model": model,
        "ok": False, "error": None, "chars": 0, "seconds": 0.0, "facts": None, "dropped": [], "flags": [],
    }
    started = time.monotonic()
    try:
        page = fetch_page(url, http)
        record["chars"] = len(page.text)
        facts = ask_model(http, model, page, ollama_url)
        raw = facts.model_dump()
        raw["sports"] = [s for s in raw["sports"] if _norm(s) not in PLACEHOLDER_SPORTS]
        record["facts"], record["dropped"] = ground(raw, page.text)
        record["flags"] = review_flags(record["facts"], page.text)
        record["ok"] = True
    except FetchError as exc:
        record["error"] = f"fetch: {exc}"
    except httpx.HTTPError as exc:
        record["error"] = f"http: {exc}"
    except (ValidationError, KeyError, json.JSONDecodeError) as exc:
        record["error"] = f"model output: {exc}"
    record["seconds"] = round(time.monotonic() - started, 1)
    return record

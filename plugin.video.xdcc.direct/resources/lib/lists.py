"""
Le liste dei bot (pagine XdccManage) lette direttamente, come fa il bot del server, ma con la sola libreria standard.

- Pagina di partenza (impostazione "pagina"): i pulsanti con le sigle delle liste (lettere puntate, "A.B.C.")
  portano alle pagine delle liste; la sigla senza punti è il nome della lista negli elementi ("ABC:nome del file").
- lista.php?func=3: ultimi aggiunti, una tabella per giorno (O G G I, I E R I, L ' A L T R O   I E R I) con
  dimensione e nome del file; niente bot né pack.
- lista.php?func=1&q=...: ricerca, righe bot | pack | dimensione | data | nome del file. I mirror ripetono gli
  stessi file: qui diventano più fonti dello stesso file.

Si guardano solo file video singoli: i film a pezzi (.partN.rar) e gli archivi non compaiono.
Etichette, titoli, episodi e lingua come quelli del server (xdcc_handler del bot), così le due versioni si vedono
uguali.
"""

import re
from html.parser import HTMLParser
from urllib.parse import urljoin

_BUTTON_RE = re.compile(r"(?:[A-Z]\.){2,6}")  # sigla di una lista: R.A.I.
MIN_QUERY_LEN = 3  # con meno caratteri lista.php non risponde niente
DAYS = ("oggi", "ieri", "l'altro ieri")

# Nick e pack finiscono in comandi IRC: niente spazi né a capo (proteggono dall'iniezione di comandi)
NICK_RE = re.compile(r"[A-Za-z\[\]\\`_^{|}][A-Za-z0-9\[\]\\`_^{|}-]{0,29}")
PACK_RE = re.compile(r"#\d{1,6}")

# Formati che Kodi apre da un indirizzo mentre arrivano
PLAYABLE_RE = re.compile(r"\.(mkv|avi|mp4|m4v|ts|mpg|mpeg|wmv|mov|divx)$", re.I)
_VIDEO_EXT_RE = re.compile(r"\.(mkv|avi|mp4|m4v|ts|mpg|mpeg|wmv|mov|divx|iso)$", re.I)
_PART_RE = re.compile(r"(.+)\.part(\d+)\.rar", re.I)
_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT])B?", re.I)
_UNITS = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}

_GROUP_RE = re.compile(r"-([A-Za-z0-9]{2,12})$")
_EPISODE_RE = re.compile(r"\bS(\d{1,2})[._ ]?E(\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b", re.I)  # S01E05, S03.E01, 1x04
_RESOLUTION_RE = re.compile(r"\b(2160p|1080p|720p|576p|480p)\b", re.I)
_X265_RE = re.compile(r"[xh]\.?265|hevc", re.I)
_YEAR_RE = re.compile(r"(19|20)\d{2}")
# Parole tecniche: il titolo finisce alla prima che si incontra (confronto senza maiuscole e senza trattini)
_TITLE_STOP = {
    "ita", "italian", "eng", "sub", "subs", "webrip", "webdl", "web", "bdrip", "brrip", "bluray", "hdtv", "dvdrip",
    "dvd5", "dvd9", "dvdscr", "hdts", "ts", "cam", "md", "ld", "ac3", "eac3", "dts", "aac", "ddp", "x264", "x265",
    "h264", "h265", "hevc", "xvid", "divx", "full", "hd", "fullhd", "multi", "nfrip", "amzn", "nowtvit", "repack",
    "proper", "extended", "uncut", "4k", "uhd", "episodio", "stagione", "season", "complete", "completa",
}
_ITA_WORDS = {"ita", "italian", "italiano"}
_ENG_WORDS = {"eng", "english"}
_SUB_WORDS = {"sub", "subs", "subbed", "sottotitoli", "sottotitolato"}


class Part:
    """Un file della lista con i mirror che lo hanno: sources = [(bot, pack), ...] nell'ordine della lista."""

    def __init__(self, number, filename, size):
        self.number, self.filename, self.size, self.sources = number, filename, size, []


class Release:
    """Un file (number 0) o una release a pezzi (.partN.rar), con la data più recente tra le righe."""

    def __init__(self, name, site, date, parts):
        self.name, self.site, self.date, self.parts = name, site, date, parts

    @property
    def size(self):
        return sum(p.size for p in self.parts)

    @property
    def multipart(self):
        return self.parts[0].number > 0

    @property
    def playable(self):
        """File video singolo: l'unico che si guarda mentre arriva."""
        return not self.multipart and bool(PLAYABLE_RE.search(self.parts[0].filename))


# --- lettura delle pagine ---

class _Page(HTMLParser):
    """Link della pagina (testo e href) e tabelle class="lista" (intestazione th e righe di celle td, come testo)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.tables = [], []
        self._link = self._table = self._row = self._cell = self._th = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self._link = [attrs["href"], []]
        elif tag == "table" and "lista" in (attrs.get("class") or "").split():
            self._table = {"th": None, "rows": []}
            self.tables.append(self._table)
        elif self._table is not None:
            if tag == "tr":
                self._end_row()
                self._row = []
            elif tag in ("td", "th"):
                self._end_cell()
                if tag == "td" and self._row is not None:
                    self._cell = []
                elif tag == "th" and self._table["th"] is None:
                    self._th = []

    def _end_cell(self):
        if self._cell is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        if self._th is not None:
            self._table["th"] = "".join(self._th)
            self._th = None

    def _end_row(self):
        self._end_cell()
        if self._row is not None:
            self._table["rows"].append(self._row)
            self._row = None

    def handle_endtag(self, tag):
        if tag == "a" and self._link is not None:
            self.links.append((self._link[0], "".join(self._link[1]).strip()))
            self._link = None
        if self._table is None:
            return
        if tag in ("td", "th"):
            self._end_cell()
        elif tag == "tr":
            self._end_row()
        elif tag == "table":
            self._end_row()
            self._table = None

    def handle_data(self, data):
        if self._link is not None:
            self._link[1].append(data)
        for target in (self._cell, self._th):
            if target is not None:
                target.append(data)


def _page(html):
    parser = _Page()
    parser.feed(html)
    parser.close()
    return parser


def parse_size(text):
    """Dimensione della lista ("1.6G", "700M") in byte; 0 se non si capisce."""
    m = _SIZE_RE.fullmatch(text.strip())
    if not m:
        return 0
    try:
        return int(float(m[1]) * _UNITS[m[2].upper()])
    except ValueError:
        return 0


def parse_start_page(html, base_url):
    """Pagina di partenza -> {"ABC": url della lista A.B.C., ...} dai pulsanti con le sigle puntate."""
    pages = {}
    for href, text in _page(html).links:
        label = re.sub(r"\s+", "", text)
        if _BUTTON_RE.fullmatch(label):
            pages.setdefault(label.replace(".", ""), urljoin(base_url, href))
    return pages


def parse_latest(html, site):
    """Risposta di func=3 -> [(lista, giorno, dimensione, nome del file)], nell'ordine della pagina."""
    files = []
    for table in _page(html).tables:
        key = "".join((table["th"] or "").split()).lower()  # "O G G I" -> "oggi"
        day = {"l'altroieri": "l'altro ieri"}.get(key, key)
        for cells in table["rows"]:
            if len(cells) == 2 and cells[1] and cells[0] != "Dimensione":
                files.append((site, day, parse_size(cells[0]), cells[1]))
    return files


def by_day(files):
    """Novità di tutte le liste insieme, dal giorno più recente (le pagine non dicono l'ora)."""
    return sorted(files, key=lambda f: DAYS.index(f[1]) if f[1] in DAYS else len(DAYS))


def parse_search(html, site):
    """Risposta di func=1 -> [(lista, bot, pack, dimensione, data, nome)]. Via le righe con nick o pack strani:
    finirebbero in comandi IRC."""
    rows = []
    for cells in (cells for table in _page(html).tables for cells in table["rows"]):
        if len(cells) < 5:
            continue
        bot, pack, size, date, filename = cells[:5]
        if NICK_RE.fullmatch(bot) and PACK_RE.fullmatch(pack) and filename:
            rows.append((site, bot, pack, size, date, filename))
    return rows


def _date_key(date):
    """gg-mm-aaaa -> aaaammgg, per ordinare."""
    parts = date.split("-")
    return "".join(reversed(parts)) if len(parts) == 3 else ""


def group_releases(rows):
    """Righe della ricerca -> release (pezzi raggruppati, mirror come fonti), le più recenti prima."""
    groups, dates = {}, {}
    for site, bot, pack, size, date, filename in rows:
        m = _PART_RE.fullmatch(filename)
        name, number = (m[1], int(m[2])) if m else (filename, 0)
        key = (site, name)
        parts = groups.setdefault(key, {})
        part = parts.get(number)
        if part is None:
            part = parts[number] = Part(number, filename, parse_size(size))
        if (bot, pack) not in part.sources:
            part.sources.append((bot, pack))
        if _date_key(date) > _date_key(dates.get(key, "")):
            dates[key] = date
    releases = [Release(name, site, dates.get((site, name), ""), [parts[n] for n in sorted(parts)])
                for (site, name), parts in groups.items()]
    releases.sort(key=lambda r: r.name.lower())
    releases.sort(key=lambda r: _date_key(r.date), reverse=True)
    return releases


# --- titoli ed etichette (come xdcc_handler del bot) ---

def fmt_size(n):
    """Byte -> "1,6 GB", "734 MB", "12 KB"."""
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB".replace(".", ",")
    if n >= 1024 ** 2:
        return f"{n / 1024 ** 2:.0f} MB"
    return f"{max(n, 0) / 1024:.0f} KB"


def short_title(name):
    """Titolo leggibile dal nome del file: spazi al posto dei punti, fino all'anno compreso o alla prima parola
    tecnica (iTA, WEBRip, 1080p, S01E05, ...)."""
    words = []
    for word in re.split(r"[.\s_]+", _GROUP_RE.sub("", _VIDEO_EXT_RE.sub("", name))):
        plain = word.lower().replace("-", "")
        if not word or _EPISODE_RE.fullmatch(word) or _RESOLUTION_RE.fullmatch(word) or plain in _TITLE_STOP:
            break
        words.append(word)
        if _YEAR_RE.fullmatch(word) and len(words) > 1:
            break
    return " ".join(words) or name


def language_of(name):
    """"ENG" (inglese senza italiano), "SUB ITA" (originale coi sottotitoli italiani) o None: audio italiano, anche
    insieme ad altre lingue, o nome che non lo dice."""
    words = [w.lower() for w in re.split(r"[.\s_\-\[\]()]+", _VIDEO_EXT_RE.sub("", name)) if w]
    words = [part for w in words for part in (["sub", "ita"] if w == "subita" else [w])]
    ita_audio = sub_ita = False
    for i, word in enumerate(words):
        if word in _ITA_WORDS:
            if (i > 0 and words[i - 1] in _SUB_WORDS) or words[i + 1:i + 2] == ["subbed"]:
                sub_ita = True
            else:
                ita_audio = True
    if ita_audio:
        return None
    if sub_ita:
        return "SUB ITA"
    return "ENG" if any(w in _ENG_WORDS for w in words) else None


def episode_of(name):
    """(stagione, episodio) dal nome (S01E05, S03.E01, 1x04); None se non è un episodio."""
    m = _EPISODE_RE.search(name)
    if not m:
        return None
    season, episode = (m[1], m[2]) if m[1] else (m[3], m[4])
    return int(season), int(episode)


def show_key(title):
    """Chiave per raggruppare gli episodi di una serie: solo lettere e cifre, senza l'anno in fondo."""
    key = re.sub(r"[^a-z0-9]", "", title.lower())
    return re.sub(r"(19|20)\d{2}$", "", key) or key


def media_label(name, size):
    """Etichetta per Kodi: titolo · episodio · lingua · risoluzione · x265 · dimensione · gruppo."""
    info = []
    ep = episode_of(name)
    if ep:
        info.append(f"S{ep[0]:02d}E{ep[1]:02d}")
    language = language_of(name)
    if language:
        info.append(language)
    m = _RESOLUTION_RE.search(name)
    if m:
        info.append(m[1].lower())
    if _X265_RE.search(name):
        info.append("x265")
    if size:  # (non si sa per gli episodi in «Continua a guardare»)
        info.append(fmt_size(size))
    group = _GROUP_RE.search(_VIDEO_EXT_RE.sub("", name))
    if group:
        info.append(group[1])
    return f"{short_title(name)} · {' · '.join(info)}"


# --- elementi delle liste per l'addon (stessa forma dell'API del server) ---

def item(site, filename, size, day=""):
    ep = episode_of(filename)
    return {"id": f"{site}:{filename}", "titolo": media_label(filename, size), "nome": filename, "dimensione": size,
            "giorno": day, "stagione": ep[0] if ep else None, "episodio": ep[1] if ep else None}


def shows(names):
    """Episodi -> serie (in ordine di prima comparsa), con quanti episodi."""
    found = {}
    for name in names:
        title = short_title(name)
        show = found.setdefault(show_key(title), {"titolo": title, "cerca": title, "episodi": 0})
        show["episodi"] += 1
    return list(found.values())


def latest_films(files):
    seen, items = set(), []
    for site, day, size, filename in files:
        if PLAYABLE_RE.search(filename) and not episode_of(filename) and filename not in seen:
            seen.add(filename)
            items.append(item(site, filename, size, day))
    return items


def latest_shows(files):
    return shows([f[3] for f in files if PLAYABLE_RE.search(f[3]) and episode_of(f[3])])


def in_show(query, name):
    """Il file è della serie cercata? Il titolo combacia (una ricerca per parole porta anche altro)."""
    wanted, key = show_key(query), show_key(short_title(name))
    return wanted in key or key in wanted


def seasons(query, releases):
    """Episodi della serie cercata, divisi per stagione: solo quelli il cui titolo combacia."""
    by_season = {}
    for r in releases:
        ep = episode_of(r.name)
        if r.playable and ep and in_show(query, r.name):
            by_season.setdefault(ep[0], []).append((ep[1], item(r.site, r.parts[0].filename, r.size, r.date)))
    return [{"numero": season, "episodi": [it for _, it in sorted(eps, key=lambda e: (e[0], e[1]["titolo"]))]}
            for season, eps in sorted(by_season.items())]


def search_results(releases, kind):
    """Risultati della ricerca per l'addon: film (file video singoli) o serie (raggruppate)."""
    playable = [r for r in releases if r.playable]
    if kind == "serie":
        return shows([r.name for r in playable if episode_of(r.name)])
    return [item(r.site, r.parts[0].filename, r.size, r.date) for r in playable if not episode_of(r.name)]

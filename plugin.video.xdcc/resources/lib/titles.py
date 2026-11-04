"""
Titolo da cercare su TMDB ricavato dal nome del file: lo stesso del server (xdcc_handler del bot) e dell'addon Direct
(plugin.video.xdcc.direct/resources/lib/lists.py), così le schede sono le stesse.
"""

import re

_VIDEO_EXT_RE = re.compile(r"\.(mkv|avi|mp4|m4v|ts|mpg|mpeg|wmv|mov|divx|iso)$", re.I)
_GROUP_RE = re.compile(r"-([A-Za-z0-9]{2,12})$")
_EPISODE_RE = re.compile(r"\bS(\d{1,2})[._ ]?E(\d{1,3})\b|\b(\d{1,2})x(\d{2,3})\b", re.I)  # S01E05, S03.E01, 1x04
_RESOLUTION_RE = re.compile(r"\b(2160p|1080p|720p|576p|480p)\b", re.I)
_YEAR_RE = re.compile(r"(19|20)\d{2}")
# Parole tecniche: il titolo finisce alla prima che si incontra (confronto senza maiuscole e senza trattini)
_TITLE_STOP = {
    "ita", "italian", "eng", "sub", "subs", "webrip", "webdl", "web", "bdrip", "brrip", "bluray", "hdtv", "dvdrip",
    "dvd5", "dvd9", "dvdscr", "hdts", "ts", "cam", "md", "ld", "ac3", "eac3", "dts", "aac", "ddp", "x264", "x265",
    "h264", "h265", "hevc", "xvid", "divx", "full", "hd", "fullhd", "multi", "nfrip", "amzn", "nowtvit", "repack",
    "proper", "extended", "uncut", "4k", "uhd", "episodio", "stagione", "season", "complete", "completa",
}


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

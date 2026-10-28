"""
Indice (Cues) di un file MKV letto dall'inizio e dalla fine del file già in RAM: da quale byte comincia un certo
secondo del video. Serve alla ripresa ("Riprendi da 21:26"): il servizio scarica quel punto prima di far partire
Kodi, che poi lo trova già pronto.

Matroska è EBML: ogni elemento è un ID (1-4 byte) e una dimensione (1-8 byte) a lunghezza variabile, poi i dati.
Inizio del file: EBML header, Segment (con dentro SeekHead, che dice dove sta l'indice, e Info, con la scala dei
tempi). L'indice (di solito in fondo al file): CuePoint con CueTime e CueClusterPosition, relativa all'inizio dei dati
del Segment. Se qualcosa non torna: nessun punto (la ripresa funziona lo stesso, solo senza anticipo).
"""

_EBML, _SEGMENT, _SEEK_HEAD, _SEEK, _SEEK_ID, _SEEK_POSITION = 0x1A45DFA3, 0x18538067, 0x114D9B74, 0x4DBB, 0x53AB, 0x53AC
_INFO, _TIMECODE_SCALE, _CLUSTER = 0x1549A966, 0x2AD7B1, 0x1F43B675
_CUES, _CUE_POINT, _CUE_TIME, _CUE_TRACK_POSITIONS, _CUE_CLUSTER_POSITION = 0x1C53BB6B, 0xBB, 0xB3, 0xB7, 0xF1


class _Short(Exception):
    """I dati finiscono prima dell'elemento."""


def _vint(data, pos, marker):
    """Intero EBML a lunghezza variabile da pos -> (valore, posizione dopo). marker: l'ID tiene il bit di lunghezza,
    la dimensione no (e "tutti uno" vuol dire dimensione sconosciuta: None)."""
    if pos >= len(data):
        raise _Short()
    first = data[pos]
    length = 1
    while length <= 8 and not first & (0x80 >> (length - 1)):
        length += 1
    if length > 8 or pos + length > len(data):
        raise _Short()
    value = first if marker else first & (0xFF >> length)
    for byte in data[pos + 1:pos + length]:
        value = (value << 8) | byte
    if not marker and value == (1 << (7 * length)) - 1:
        value = None
    return value, pos + length


def _element(data, pos):
    """-> (id, dimensione o None, inizio dei dati)."""
    element_id, pos = _vint(data, pos, True)
    size, pos = _vint(data, pos, False)
    return element_id, size, pos


def _children(data, start, end):
    """Figli di un elemento con i dati in data[start:end]: (id, inizio, fine dei dati)."""
    pos = start
    while pos < end:
        element_id, size, data_start = _element(data, pos)
        if size is None or data_start + size > end:
            return
        yield element_id, data_start, data_start + size
        pos = data_start + size


def _uint(data, start, end):
    return int.from_bytes(bytes(data[start:end]), "big") if end > start else 0


def _segment(head):
    """Dall'inizio del file: (inizio dei dati del Segment, posizione dell'indice nel Segment, scala dei tempi in ns)."""
    element_id, size, pos = _element(head, 0)
    if element_id != _EBML or size is None:
        return None
    element_id, _, segment_start = _element(head, pos + size)
    if element_id != _SEGMENT:
        return None
    cues, scale, pos = None, 1000000, segment_start
    try:
        while pos < len(head):
            element_id, size, data_start = _element(head, pos)
            if element_id == _CLUSTER or size is None:
                break
            end = data_start + size
            if end > len(head):
                break
            if element_id == _SEEK_HEAD:
                for child, cstart, cend in _children(head, data_start, end):
                    if child != _SEEK:
                        continue
                    seek_id = seek_pos = None
                    for field, fstart, fend in _children(head, cstart, cend):
                        if field == _SEEK_ID:
                            seek_id = _uint(head, fstart, fend)
                        elif field == _SEEK_POSITION:
                            seek_pos = _uint(head, fstart, fend)
                    if seek_id == _CUES and seek_pos is not None and cues is None:
                        cues = seek_pos
            elif element_id == _INFO:
                for field, fstart, fend in _children(head, data_start, end):
                    if field == _TIMECODE_SCALE:
                        scale = _uint(head, fstart, fend) or scale
            pos = end
    except _Short:
        pass
    return segment_start, cues, scale


def cue_points(head, tail, tail_start):
    """[(secondi, byte nel file)] dall'indice, in ordine; [] se non si trova o non è tutto in RAM."""
    try:
        found = _segment(head)
        if not found or found[1] is None:
            return []
        segment_start, cues_pos, scale = found
        at = segment_start + cues_pos
        if tail is not None and at >= tail_start:
            data, base = tail, tail_start
        elif at < len(head):
            data, base = head, 0
        else:
            return []
        element_id, size, start = _element(data, at - base)
        if element_id != _CUES or size is None or start + size > len(data):
            return []
        points = []
        for child, cstart, cend in _children(data, start, start + size):
            if child != _CUE_POINT:
                continue
            time, cluster = None, None
            for field, fstart, fend in _children(data, cstart, cend):
                if field == _CUE_TIME:
                    time = _uint(data, fstart, fend)
                elif field == _CUE_TRACK_POSITIONS and cluster is None:
                    for sub, sstart, send in _children(data, fstart, fend):
                        if sub == _CUE_CLUSTER_POSITION:
                            cluster = _uint(data, sstart, send)
            if time is not None and cluster is not None:
                points.append((time * scale / 1e9, segment_start + cluster))
        return sorted(points)
    except _Short:
        return []


def resume_position(points, seconds):
    """Da quale byte scaricare per riprendere a seconds: il punto dell'indice prima di quello in cui Kodi
    ripartirà (un margine: Kodi parte dal punto dell'indice che precede il secondo chiesto). None se nessuno."""
    before = [byte for time, byte in points if time <= seconds]
    if not before:
        return None
    return before[-2] if len(before) > 1 else before[-1]

"""Indice di un MKV (Cues) letto dall'inizio e dalla fine del file: MKV costruito qui, con la stessa struttura."""
from resources.lib import mkv


def _size(n):
    return bytes([0x01]) + n.to_bytes(7, "big")  # dimensione EBML a 8 byte


def element(element_id, payload):
    return element_id.to_bytes((element_id.bit_length() + 7) // 8, "big") + _size(len(payload)) + payload


def uint(element_id, value):
    return element(element_id, value.to_bytes(4, "big"))


def make_mkv(cues_at_end=True):
    """EBML header, Segment con SeekHead (-> Cues), Info (scala dei tempi 1 ms), cluster finti e l'indice in fondo."""
    clusters = b"".join(element(0x1F43B675, bytes(1000)) for _ in range(5))
    cue_points = b"".join(
        element(0xBB, uint(0xB3, seconds * 1000) + element(0xB7, uint(0xF7, 1) + uint(0xF1, position)))
        for seconds, position in ((0, 100), (10, 1100), (20, 2100), (30, 3100)))
    cues = element(0x1C53BB6B, cue_points)
    info = element(0x1549A966, uint(0x2AD7B1, 1000000))
    seek_head_len = len(element(0x114D9B74, element(0x4DBB, element(0x53AB, (0x1C53BB6B).to_bytes(4, "big"))
                                                        + uint(0x53AC, 0))))
    cues_pos = seek_head_len + len(info) + len(clusters)
    seek_head = element(0x114D9B74, element(0x4DBB, element(0x53AB, (0x1C53BB6B).to_bytes(4, "big"))
                                            + uint(0x53AC, cues_pos)))
    segment_payload = seek_head + info + clusters + cues
    header = element(0x1A45DFA3, element(0x4282, b"matroska"))
    segment = (0x18538067).to_bytes(4, "big") + bytes([0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])  # sconosciuta
    data = header + segment + segment_payload
    return data, len(header) + len(segment)


def test_indice_dall_inizio_e_dalla_fine():
    data, segment_start = make_mkv()
    head, tail_start = data[:400], len(data) - 300
    points = mkv.cue_points(head, data[tail_start:], tail_start)
    assert points == [(0.0, segment_start + 100), (10.0, segment_start + 1100), (20.0, segment_start + 2100),
                      (30.0, segment_start + 3100)]
    # ripresa a 25 s: Kodi parte dal punto dei 20 s; si scarica da quello prima (10 s), per margine
    assert mkv.resume_position(points, 25.5) == segment_start + 1100
    assert mkv.resume_position(points, 5) == segment_start + 100


def test_indice_che_non_si_trova():
    data, _ = make_mkv()
    assert mkv.cue_points(data[:400], data[-50:], len(data) - 50) == []  # la fine in RAM non arriva all'indice
    assert mkv.cue_points(b"non e un mkv" * 10, data[-300:], len(data) - 300) == []
    assert mkv.cue_points(data[:20], None, 0) == []  # inizio troncato
    assert mkv.resume_position([], 100) is None

"""Export lyric decks as native ProPresenter 7 presentations (.pro files).

Builds a Presentation protobuf (schema: github.com/greyshirtguy/ProPresenter7-Proto,
MIT) with one slide per lyric block and writes it into the ProPresenter library,
where the app picks it up automatically. This is what lets the pipeline drive
real slides in ProPresenter instead of a mock.
"""

import base64
import re
import uuid as uuidlib
from pathlib import Path

from loguru import logger

from . import pp_proto  # noqa: F401  (sets up import path for generated modules)
from .pp_proto import presentation_pb2

DEFAULT_LIBRARY = (
    Path.home()
    / "Library/Application Support/RenewedVision/ProPresenter/UserWorkspaces/ProPresenter/Libraries/Default"
)

RTF_TEMPLATE = (
    "{\\rtf1\\ansi\\ansicpg1252\\cocoartf2870\n"
    "\\cocoatextscaling0\\cocoaplatform0"
    "{\\fonttbl\\f0\\fswiss\\fcharset0 HelveticaNeue-Bold;}\n"
    "{\\colortbl;\\red255\\green255\\blue255;}\n"
    "{\\*\\expandedcolortbl;;}\n"
    "\\pard\\qc\\partightenfactor0\n\n"
    "\\f0\\b\\fs120 \\cf1 %s}"
)


def _rtf_escape(text: str) -> str:
    out = []
    for ch in text:
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ord(ch) > 127:
            out.append(f"\\u{ord(ch)}?")
        else:
            out.append(ch)
    return "".join(out)


def _uuid() -> str:
    return str(uuidlib.uuid4()).upper()


# A cue serialized from a deck ProPresenter itself authored, then verified to
# round-trip: PP parses it into a real slide and triggers it over the API.
# Hand-built cues that *look* structurally identical parse to zero slides —
# PP is picky in ways the schema doesn't express — so we clone this template
# and swap uuids + rtf_data instead of constructing cues field by field.
TEMPLATE_CUE_B64 = (
    "CiYKJDI3NEJFMjMxLTQ4QzUtNDFEMy04MjVELTE5Q0UyRUY2RjNBRigBQgBSyAYKJgokODI5Q0JGM0UtRUMzRC00RkMwLUE3"
    "MEYtRjAwRTg2OTEzMkJDMAFIC7oBmAYSlQYKjgYKzwUKtAUKJgokMjU1MkE2MkYtQjM4Qy00MEY3LTgwRDQtODk0QjU3M0Mw"
    "NkZCGigKEgkAAAAAAABOQBEAAAAAAABOQBISCQAAAAAAIJxAEQAAAAAAAI5AKQAAAAAAAPA/QpIBCAESBgoAEgAaABIhCgkJ"
    "AAAAAAAA8D8SCQkAAAAAAADwPxoJCQAAAAAAAPA/EjwKEgkAAAAAAADwPxEAAAAAAADwPxISCQAAAAAAAPA/EQAAAAAAAPA/"
    "GhIJAAAAAAAA8D8RAAAAAAAA8D8SIQoJEQAAAAAAAPA/EgkRAAAAAAAA8D8aCREAAAAAAADwPxoCCAFKAFIAWgBiAGq3Axpm"
    "Ci8KEkhlbHZldGljYU5ldWUtQm9sZBEAAAAAAABOQEABSg5IZWx2ZXRpY2EgTmV1ZRoUDQAAgD8VAACAPx0AAIA/JQAAgD8i"
    "ADIWCAIpAAAAAAAA8D9hAAAAAAAAVUBqAEoAmAEBIgAqowJ7XHJ0ZjFcYW5zaVxhbnNpY3BnMTI1Mlxjb2NvYXJ0ZjI4NzAK"
    "XGNvY29hdGV4dHNjYWxpbmcwXGNvY29hcGxhdGZvcm0we1xmb250dGJsXGYwXGZzd2lzc1xmY2hhcnNldDAgSGVsdmV0aWNh"
    "TmV1ZS1Cb2xkO30Ke1xjb2xvcnRibDtccmVkMjU1XGdyZWVuMjU1XGJsdWUyNTU7fQp7XCpcZXhwYW5kZWRjb2xvcnRibDs7"
    "fQpccGFyZFxxY1xwYXJ0aWdodGVuZmFjdG9yMAoKXGYwXGJcZnMxMjAgXGNmMSBNeSBsaWZlIGlzIGEgc3RvcnkgYWJvdXQg"
    "aG93IEkgd2FzIHRoaW5raW5nIHdoZW4gSSBkaWQgaXQgZG9uZX0wAUIASAFaByAg4oCiICBiFhoUDT81fj8VXI9CPx1vEgM9"
    "JQAAgD9yACADShQRAAAAAAAA4D8YASFYpAw83ZqvPzISCQAAAAAAAJ5AEQAAAAAA4JBAOiYKJDBGNUNFRjU0LTJCREItNDYx"
    "MC04MEEzLUYwNkE2NEUyRkREQyICGAFgAQ=="
)


def _template_cue():
    from .pp_proto import cue_pb2
    cue = cue_pb2.Cue()
    cue.ParseFromString(base64.b64decode(TEMPLATE_CUE_B64))
    return cue


# Presentation skeleton from the same PP-authored deck (cues stripped) — the
# header fields PP wrote, verified to parse. Hand-built headers yield decks
# whose cues PP silently drops.
TEMPLATE_PRES_B64 = "Ch0IARIGCBoQBRgBGAEiDwgVEAMiCTM1MjUxODE3OBIAQgBKAhgBcgCKAQA="


def build_presentation(name: str, slides: list[list[str]]) -> "presentation_pb2.Presentation":
    """Build a Presentation with one text slide per slide block."""
    p = presentation_pb2.Presentation()
    p.ParseFromString(base64.b64decode(TEMPLATE_PRES_B64))
    # Deterministic per name: PP indexes a .pro by the uuid it first saw and
    # 404s if an overwrite changes it, so re-exports must keep the identity.
    p.uuid.string = str(uuidlib.uuid5(uuidlib.NAMESPACE_URL, "autolyrics:" + name)).upper()
    p.name = name

    group = p.cue_groups.add()
    group.group.uuid.string = _uuid()
    group.group.name = "Lyrics"
    group.group.hotKey.SetInParent()

    tpl = _template_cue()
    for lines in slides:
        cue = p.cues.add()
        cue.CopyFrom(tpl)
        cue.uuid.string = _uuid()
        cue.actions[0].uuid.string = _uuid()
        sl = cue.actions[0].slide.presentation.base_slide
        sl.uuid.string = _uuid()
        sl.elements[0].element.uuid.string = _uuid()
        body = "\\\n".join(_rtf_escape(l) for l in lines)
        sl.elements[0].element.text.rtf_data = (RTF_TEMPLATE % body).encode()
        group.cue_identifiers.add().string = cue.uuid.string
    return p


def export_song(name: str, lyrics_text: str, library: Path = DEFAULT_LIBRARY) -> Path:
    """Write a .pro presentation for blank-line-separated slide lyrics."""
    slides: list[list[str]] = []
    block: list[str] = []
    for raw in lyrics_text.split("\n"):
        line = raw.strip()
        if line:
            block.append(line)
        elif block:
            slides.append(block)
            block = []
    if block:
        slides.append(block)

    pres = build_presentation(name, slides)
    safe = re.sub(r'[/:]', "-", name)
    out = library / f"{safe}.pro"
    out.write_bytes(pres.SerializeToString())
    logger.info(f"Exported '{name}' ({len(slides)} slides) → {out}")
    return out


if __name__ == "__main__":
    import sys
    path = Path(sys.argv[1])
    export_song(sys.argv[2] if len(sys.argv) > 2 else path.stem, path.read_text())

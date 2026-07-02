"""Export lyric decks as native ProPresenter 7 presentations (.pro files).

Builds a Presentation protobuf (schema: github.com/greyshirtguy/ProPresenter7-Proto,
MIT) with one slide per lyric block and writes it into the ProPresenter library,
where the app picks it up automatically. This is what lets the pipeline drive
real slides in ProPresenter instead of a mock.
"""

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


def build_presentation(name: str, slides: list[list[str]]) -> "presentation_pb2.Presentation":
    """Build a Presentation with one text slide per slide block."""
    p = presentation_pb2.Presentation()
    ai = p.application_info
    ai.platform = ai.PLATFORM_MACOS
    ai.application = ai.APPLICATION_PROPRESENTER
    ai.application_version.major_version = 21
    ai.application_version.minor_version = 3
    p.uuid.string = _uuid()
    p.name = name
    p.background.SetInParent()
    p.chord_chart.platform = p.chord_chart.PLATFORM_MACOS

    group = p.cue_groups.add()
    group.group.uuid.string = _uuid()
    group.group.name = "Lyrics"
    group.group.hotKey.SetInParent()

    for lines in slides:
        cue = p.cues.add()
        cue.uuid.string = _uuid()
        cue.completion_action_type = cue.COMPLETION_ACTION_TYPE_LAST
        cue.hot_key.SetInParent()
        gid = group.cue_identifiers.add()
        gid.string = cue.uuid.string

        action = cue.actions.add()
        action.uuid.string = _uuid()
        action.isEnabled = True
        action.type = action.ACTION_TYPE_PRESENTATION_SLIDE

        slide = action.slide.presentation.base_slide
        slide.uuid.string = _uuid()
        slide.size.width = 1920
        slide.size.height = 1080
        slide.draws_background_color = False

        el = slide.elements.add().element
        el.uuid.string = _uuid()
        el.name = "Lyrics"
        el.bounds.origin.x = 60
        el.bounds.origin.y = 60
        el.bounds.size.width = 1800
        el.bounds.size.height = 960
        el.opacity = 1.0
        el.fill.SetInParent()

        text = el.text
        f = text.attributes.font
        f.name = "HelveticaNeue-Bold"
        f.size = 60
        f.family = "Helvetica Neue"
        f.bold = True
        fill = text.attributes.text_solid_fill
        fill.red = fill.green = fill.blue = fill.alpha = 1.0
        ps = text.attributes.paragraph_style
        ps.alignment = ps.ALIGNMENT_CENTER
        ps.line_height_multiple = 1.0
        text.vertical_alignment = text.VERTICAL_ALIGNMENT_MIDDLE
        text.is_superscript_standardized = True
        body = "\\\n".join(_rtf_escape(l) for l in lines)
        text.rtf_data = (RTF_TEMPLATE % body).encode()
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

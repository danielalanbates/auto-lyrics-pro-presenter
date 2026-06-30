"""Create (or overwrite) a test presentation in ProPresenter, programmatically.

Drives ProPresenter's File → Import → Text from Clipboard so we get a real,
API-visible presentation with one slide per line — no protobuf/.pro authoring.
ProPresenter splits slides on blank lines ("Paragraph Break"), so each slide is
separated by a blank line on the clipboard.

Usage:
    python tools/make_test_song.py            # default 8-line Amazing Grace
    python tools/make_test_song.py my.txt     # slides = paragraphs of my.txt

Requires Accessibility permission for whatever runs it (Terminal), and the
ProPresenter Network API on (for the verification step).
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config
from src.propresenter_bridge import ProPresenterBridge

DEFAULT_SLIDES = [
    "Amazing grace how sweet the sound",
    "That saved a wretch like me",
    "I once was lost but now am found",
    "Was blind but now I see",
    "My chains are gone I have been set free",
    "My God my Savior has ransomed me",
    "And like a flood His mercy reigns",
    "Unending love amazing grace",
]

# AppleScript that recursively finds a button by title anywhere in PP's windows
# and presses it (PP's SwiftUI tree is opaque to `entire contents`, so we recurse).
PRESS_BUTTON = '''
global theBtn
set theBtn to missing value
tell application "System Events" to tell process "ProPresenter"
  repeat with w in windows
    my findBtn(w, 0)
  end repeat
  if theBtn is missing value then return "NOTFOUND"
  perform action "AXPress" of theBtn
  return "OK"
end tell
on findBtn(el, depth)
  using terms from application "System Events"
    if theBtn is not missing value then return
    try
      if (role of el) is "AXButton" and (name of el) is "%s" then
        set theBtn to el
        return
      end if
    end try
    if depth < 12 then
      try
        repeat with c in (UI elements of el)
          my findBtn(c, depth + 1)
          if theBtn is not missing value then return
        end repeat
      end try
    end if
  end using terms from
end findBtn
'''


def osa(script: str) -> str:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True).stdout.strip()


def press_button(title: str) -> str:
    return osa(PRESS_BUTTON % title)


def main():
    if len(sys.argv) > 1:
        text = open(sys.argv[1]).read()
        slides = [p.strip() for p in text.split("\n\n") if p.strip()]
    else:
        slides = DEFAULT_SLIDES
    n = len(slides)
    print(f"Creating test presentation with {n} slides")

    # Clipboard: blank line between slides == one slide per paragraph.
    subprocess.run(["pbcopy"], input="\n\n".join(slides), text=True, check=True)

    osa('tell application "ProPresenter" to activate')
    time.sleep(1.0)
    osa(
        'tell application "System Events" to tell process "ProPresenter" to '
        'click menu item "Text from Clipboard…" of menu 1 of menu item "Import" '
        'of menu 1 of menu bar item "File" of menu bar 1'
    )
    time.sleep(2.0)
    import_result = "NOTFOUND"
    for _ in range(5):
        import_result = press_button("Import")
        if import_result == "OK":
            break
        time.sleep(1.0)
    print("Import dialog:", import_result)
    time.sleep(1.5)
    # If the presentation already exists, PP asks; overwrite it.
    over = press_button("Write Over")
    if over == "OK":
        print("Existing presentation: Write Over")
        time.sleep(1.5)

    # Verify via the API.
    cfg = load_config()
    bridge = ProPresenterBridge(cfg.propresenter)
    if not bridge.connect():
        print("WARN: could not verify via API (Network API off?)")
        return 0
    # The new presentation becomes focused; trigger its first slide to make it live.
    got = bridge.get_song_slides()
    print(f"API reports {len(got)} slides in the live presentation:")
    for i, s in enumerate(got):
        print(f"   [{i}] {s}")
    ok = len(got) == n
    print("RESULT:", "PASS" if ok else f"MISMATCH (expected {n})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

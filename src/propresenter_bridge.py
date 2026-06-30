"""ProPresenter bridge — talks to ProPresenter 7's built-in HTTP API.

Uses only the Python standard library (urllib) so the dependency footprint stays
permissive/MIT-compatible — no third-party HTTP client.

Every endpoint here was verified live against ProPresenter 21.4 (api_version v1)
on 2026-06-28. The exact shapes are documented inline so future drift is easy to
spot.

Verified endpoints (GET unless noted):
  /version                                  -> {name, platform, os_version, api_version}
  /v1/presentation/active                   -> {presentation:{id, groups:[{slides:[{text,...}]}]}}
  /v1/presentation/slide_index             -> {presentation_index:{index, total_cues, ...}}
  /v1/status/slide                          -> {current:{text,uuid}, next:{text,uuid}}
  /v1/presentation/active/{index}/trigger   -> 204  (jump live to slide `index`)
  /v1/trigger/next | /v1/trigger/previous   -> 204  (step the live presentation)

The port is NOT fixed: ProPresenter assigns one (Settings → Network → Port).
On this machine it is 62595. `discover_port()` finds it if it ever changes.
"""

import base64
import json
import urllib.error
import urllib.request
from typing import Optional

from loguru import logger

from .config import ProPresenterConfig

# Ports to probe if the configured one doesn't answer /version.
_CANDIDATE_PORTS = [62595, 1025, 50001, 1024, 8080]


def _http_get(url: str, timeout: float = 5.0, auth_header: Optional[str] = None):
    """Return (status_code, body_text). status_code is None on a transport error."""
    req = urllib.request.Request(url, method="GET")
    if auth_header:
        req.add_header("Authorization", auth_header)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError, ValueError):
        return None, ""


def discover_port(host: str = "127.0.0.1", prefer: Optional[int] = None) -> Optional[int]:
    """Return the port ProPresenter's API is answering on, or None.

    Tries `prefer` first, then a small candidate list. We confirm with /version
    rather than a bare TCP connect so we don't latch onto an unrelated service.
    """
    ports = ([prefer] if prefer else []) + [p for p in _CANDIDATE_PORTS if p != prefer]
    for port in ports:
        status, body = _http_get(f"http://{host}:{port}/version", timeout=1.5)
        if status == 200 and "api_version" in body:
            return port
    return None


class ProPresenterBridge:
    """Communicates with ProPresenter 7 via its built-in REST API."""

    def __init__(self, config: ProPresenterConfig):
        self.config = config
        self.port = config.port
        self._base = f"http://{config.host}:{self.port}/v1"
        self._auth = None
        if config.password:
            # PP7 uses Basic auth with an empty username.
            token = base64.b64encode(f":{config.password}".encode()).decode()
            self._auth = f"Basic {token}"
        logger.info(f"ProPresenter bridge ready: {self._base}")

    # ---- connection --------------------------------------------------------

    def connect(self, auto_discover: bool = True) -> bool:
        """Confirm the API is reachable; optionally rediscover the port."""
        if self._version_ok():
            logger.info(f"ProPresenter API OK on port {self.port}")
            return True
        if auto_discover:
            found = discover_port(self.config.host, prefer=self.port)
            if found:
                self._set_port(found)
                logger.warning(f"ProPresenter API found on port {found} (rediscovered)")
                return True
        logger.error(
            f"Cannot reach ProPresenter on {self.config.host}. Is it running with "
            "Settings → Network → Enable Network turned on?"
        )
        return False

    def _set_port(self, port: int):
        self.port = port
        self._base = f"http://{self.config.host}:{port}/v1"

    def _version_ok(self) -> bool:
        status, body = self._get(f"http://{self.config.host}:{self.port}/version", timeout=2)
        return status == 200 and "api_version" in body

    def _get(self, url: str, timeout: float = 5.0):
        return _http_get(url, timeout=timeout, auth_header=self._auth)

    def _get_json(self, path: str, timeout: float = 5.0) -> dict:
        status, body = self._get(f"{self._base}{path}", timeout=timeout)
        if status == 200 and body:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                logger.error(f"bad JSON from {path}: {body[:120]}")
        return {}

    # ---- control -----------------------------------------------------------

    def go_to_slide(self, slide_index: int) -> bool:
        """Jump the LIVE presentation to a 0-based slide index. Verified: 204."""
        status, _ = self._get(f"{self._base}/presentation/active/{slide_index}/trigger")
        if status in (200, 204):
            logger.info(f"→ slide {slide_index}")
            return True
        logger.error(f"go_to_slide {slide_index} failed: {status}")
        return False

    def advance_slide(self, direction: str = "next") -> bool:
        """Step the live presentation. direction: 'next' | 'prev'."""
        endpoint = "next" if direction == "next" else "previous"
        status, _ = self._get(f"{self._base}/trigger/{endpoint}")
        if status in (200, 204):
            logger.debug(f"trigger {endpoint}")
            return True
        logger.error(f"advance {endpoint} failed: {status}")
        return False

    # ---- status / answer key ----------------------------------------------

    def get_status(self) -> dict:
        """Live {current:{text,uuid}, next:{text,uuid}} for the active slide."""
        return self._get_json("/status/slide")

    def get_active_presentation(self) -> dict:
        """The currently live presentation object (id + groups + slides)."""
        return self._get_json("/presentation/active")

    def get_song_slides(self) -> list[str]:
        """The matcher's "answer key": ordered slide texts of the live song.

        Verified against ProPresenter 21.4 — the API returns full per-slide text
        under presentation.groups[].slides[].text, so this is reliable (no need
        for the old protobuf/.pro parsing fallback). Returns [] if nothing live.
        """
        active = self.get_active_presentation()
        pres = active.get("presentation", active) if isinstance(active, dict) else {}
        return self._extract_slide_texts(pres)

    @staticmethod
    def _extract_slide_texts(pres: dict) -> list[str]:
        """Pull ordered slide text from a presentation object. Tolerant of the
        field shapes PP has used (`text`, `base_text`, nested `elements`)."""
        if not isinstance(pres, dict):
            return []
        out: list[str] = []
        for group in pres.get("groups", []) or []:
            for slide in group.get("slides", []) or []:
                if not isinstance(slide, dict):
                    continue
                txt = slide.get("text") or slide.get("base_text") or ""
                if not txt:
                    parts = [
                        e.get("text", "")
                        for e in slide.get("elements", []) or []
                        if isinstance(e, dict)
                    ]
                    txt = " ".join(p for p in parts if p)
                # PP may pack a whole multi-line slide into one text blob; the
                # caller treats each returned entry as one slide, so collapse
                # internal whitespace but keep the slide as one unit.
                txt = " ".join(txt.split())
                if txt:
                    out.append(txt)
        return out

    def get_current_slide_index(self) -> Optional[int]:
        """0-based index of the live slide, or None.

        Verified shape: {"presentation_index": {"index": N, "total_cues": ...}}.
        """
        info = self.get_slide_index_info()
        idx = info.get("index")
        return idx if isinstance(idx, int) else None

    def get_slide_index_info(self) -> dict:
        """Full live-position info: {index, total_cues, remaining_cues, uuid}."""
        data = self._get_json("/presentation/slide_index")
        pi = data.get("presentation_index", data) if isinstance(data, dict) else {}
        if isinstance(pi, dict):
            pid = pi.get("presentation_id", {})
            return {
                "index": pi.get("index"),
                "total_cues": pi.get("total_cues"),
                "remaining_cues": pi.get("remaining_cues"),
                "uuid": pid.get("uuid") if isinstance(pid, dict) else None,
            }
        return {}

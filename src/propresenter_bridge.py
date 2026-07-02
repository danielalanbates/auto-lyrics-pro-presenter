"""ProPresenter bridge — drives slides over ProPresenter 7's official HTTP API.

The v1 API (Preferences → Network → Enable Network) exposes trigger endpoints
and, critically, state read-back: after every trigger we can ask ProPresenter
which slide is actually live, so verification never relies on assumptions.

OSC remains available as a fallback transport (fire-and-forget, no read-back).
"""

import time
from typing import Optional

import requests
from loguru import logger

from .config import ProPresenterConfig


class ProPresenterBridge:
    """Communicates with ProPresenter to advance slides."""

    def __init__(self, config: ProPresenterConfig):
        self.config = config
        self._osc_client = None
        self.presentation_uuid: Optional[str] = None  # target presentation

        if config.use_osc:
            self._init_osc()

    # ------------------------------------------------------------------ HTTP

    def _url(self, path: str) -> str:
        return f"http://{self.config.host}:{self.config.http_port}/{path.lstrip('/')}"

    def _get(self, path: str, timeout: float = 3.0) -> Optional[dict]:
        try:
            resp = requests.get(self._url(path), timeout=timeout)
            if resp.status_code // 100 == 2:
                return resp.json() if resp.content else {}
            logger.error(f"PP API {path}: HTTP {resp.status_code}")
        except requests.RequestException as e:
            logger.error(f"PP API {path}: {e}")
        return None

    @staticmethod
    def discover_port() -> Optional[int]:
        """Find ProPresenter's Network API port from its listening sockets.

        PP 7/21 picks an ephemeral port each launch, so a static config value
        goes stale; discovery from lsof is the reliable path on the same Mac.
        """
        import re
        import subprocess
        r = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-c", "ProPresenter"],
            capture_output=True, text=True,
        )
        for port in sorted({int(m) for m in re.findall(r":(\d+) \(LISTEN\)", r.stdout)}):
            try:
                resp = requests.get(f"http://127.0.0.1:{port}/version", timeout=2)
                if resp.ok and "api_version" in resp.text:
                    return port
            except requests.RequestException:
                continue
        return None

    def connect(self) -> bool:
        """Confirm the API is reachable; log the ProPresenter version."""
        info = self._get("version")
        if info is None and self.config.host in ("127.0.0.1", "localhost"):
            port = self.discover_port()
            if port:
                logger.info(f"Discovered ProPresenter API on port {port}")
                self.config.http_port = port
                info = self._get("version")
        if info is not None:
            logger.info(
                f"ProPresenter API OK: {info.get('name', '?')} "
                f"{info.get('host_description', '')} api={info.get('api_version', '?')}"
            )
            return True
        if self.config.use_osc and self._osc_client:
            logger.warning("HTTP API unreachable; falling back to OSC (no read-back)")
            return True
        return False

    def find_presentation(self, name: str) -> Optional[str]:
        """Look up a presentation UUID by name across all libraries."""
        libs = self._get("v1/libraries") or []
        for lib in libs:
            items = self._get(f"v1/library/{lib['uuid']}") or {}
            for it in items.get("items", []):
                if it.get("name") == name:
                    return it["uuid"]
        return None

    def slide_count(self, uuid: str) -> int:
        """How many slides ProPresenter's parse of a presentation contains."""
        data = self._get(f"v1/presentation/{uuid}") or {}
        pres = data.get("presentation") or {}
        return sum(len(g.get("slides", [])) for g in pres.get("groups", []))

    def focus_presentation(self, uuid: str):
        """Make a presentation the target for slide triggers."""
        self.presentation_uuid = uuid

    def go_to_slide(self, slide_index: int):
        """Trigger a specific slide (0-based) on the focused/active presentation."""
        if self.presentation_uuid:
            if self._get(f"v1/presentation/{self.presentation_uuid}/{slide_index}/trigger") is not None:
                logger.info(f"PP trigger: slide {slide_index}")
                return
        elif self._get(f"v1/presentation/active/{slide_index}/trigger") is not None:
            logger.info(f"PP trigger (active): slide {slide_index}")
            return
        if self.config.use_osc and self._osc_client:
            self._osc_go_to_slide(slide_index)

    def advance_slide(self, direction: str = "next"):
        path = "v1/presentation/active/next/trigger" if direction == "next" \
            else "v1/presentation/active/previous/trigger"
        if self._get(path) is None and self.config.use_osc and self._osc_client:
            self._advance_osc(direction)

    def live_slide_index(self) -> Optional[int]:
        """Read back which slide ProPresenter is actually showing."""
        data = self._get("v1/presentation/slide_index")
        if data and data.get("presentation_index") is not None:
            return data["presentation_index"].get("index")
        return None

    def verify_slide(self, expected_index: int, retries: int = 5, delay: float = 0.2) -> bool:
        """Poll until ProPresenter reports the expected live slide."""
        for _ in range(retries):
            if self.live_slide_index() == expected_index:
                return True
            time.sleep(delay)
        return False

    def get_status(self) -> dict:
        data = self._get("v1/presentation/active")
        return data or {}

    # ------------------------------------------------------------------- OSC

    def _init_osc(self):
        try:
            from pythonosc import udp_client
            self._osc_client = udp_client.SimpleUDPClient(self.config.host, self.config.osc_port)
            logger.info(f"OSC client initialized: {self.config.host}:{self.config.osc_port}")
        except Exception as e:
            logger.error(f"Failed to initialize OSC: {e}")

    def _osc_go_to_slide(self, slide_index: int):
        try:
            self._osc_client.send_message("/slide/select", [slide_index])
            logger.info(f"OSC: jumped to slide {slide_index}")
        except Exception as e:
            logger.error(f"OSC slide select failed: {e}")

    def _advance_osc(self, direction: str):
        try:
            self._osc_client.send_message(
                "/slide/next" if direction == "next" else "/slide/prev", []
            )
        except Exception as e:
            logger.error(f"OSC advance failed: {e}")

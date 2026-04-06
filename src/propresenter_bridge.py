"""ProPresenter bridge — sends slide commands to ProPresenter via OSC or HTTP."""

from typing import Optional

from loguru import logger

from .config import ProPresenterConfig


class ProPresenterBridge:
    """Communicates with ProPresenter to advance slides."""

    def __init__(self, config: ProPresenterConfig):
        self.config = config
        self._osc_client = None
        self._session_id: Optional[str] = None

        if config.use_osc:
            self._init_osc()

    def _init_osc(self):
        """Initialize OSC client."""
        try:
            from pythonosc import udp_client
            self._osc_client = udp_client.SimpleUDPClient(
                self.config.host, self.config.osc_port
            )
            logger.info(
                f"OSC client initialized: {self.config.host}:{self.config.osc_port}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize OSC: {e}")

    def connect(self) -> bool:
        """Test connection to ProPresenter."""
        if self.config.use_osc and self._osc_client:
            try:
                # Send a ping or status request
                self._osc_client.send_message("/status", [])
                logger.info("ProPresenter OSC connection OK")
                return True
            except Exception as e:
                logger.error(f"ProPresenter OSC connection failed: {e}")
                return False
        return False

    def advance_slide(self, direction: str = "next"):
        """Advance to the next or previous slide.
        
        Args:
            direction: 'next' or 'prev'
        """
        if self.config.use_osc and self._osc_client:
            self._advance_osc(direction)
        elif self.config.use_http:
            self._advance_http(direction)
        else:
            logger.warning("No output method configured")

    def go_to_slide(self, slide_index: int):
        """Jump to a specific slide by index.
        
        Args:
            slide_index: 0-based slide index
        """
        if self.config.use_osc and self._osc_client:
            try:
                self._osc_client.send_message("/slide/select", [slide_index])
                logger.info(f"Jumped to slide {slide_index}")
            except Exception as e:
                logger.error(f"OSC slide select failed: {e}")
        elif self.config.use_http:
            self._http_go_to_slide(slide_index)

    def _advance_osc(self, direction: str):
        """Send advance command via OSC."""
        osc_address = "/slide/next" if direction == "next" else "/slide/prev"
        try:
            self._osc_client.send_message(osc_address, [])
            logger.debug(f"OSC: advanced {direction}")
        except Exception as e:
            logger.error(f"OSC advance failed: {e}")

    def _advance_http(self, direction: str):
        """Send advance command via HTTP API."""
        import requests

        url = f"http://{self.config.host}:{self.config.http_port}/api/advance"
        payload = {"direction": direction}
        if self.config.password:
            payload["password"] = self.config.password

        try:
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                logger.debug(f"HTTP: advanced {direction}")
            else:
                logger.error(f"HTTP advance failed: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"HTTP advance failed: {e}")

    def _http_go_to_slide(self, slide_index: int):
        """Jump to specific slide via HTTP API."""
        import requests

        url = f"http://{self.config.host}:{self.config.http_port}/api/slide"
        payload = {"index": slide_index}
        if self.config.password:
            payload["password"] = self.config.password

        try:
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                logger.info(f"HTTP: jumped to slide {slide_index}")
            else:
                logger.error(f"HTTP slide jump failed: {resp.status_code}")
        except Exception as e:
            logger.error(f"HTTP slide jump failed: {e}")

    def get_status(self) -> dict:
        """Get current ProPresenter status."""
        if self.config.use_http:
            import requests

            url = f"http://{self.config.host}:{self.config.http_port}/api/status"
            try:
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    return resp.json()
            except Exception as e:
                logger.error(f"HTTP status check failed: {e}")
        return {}

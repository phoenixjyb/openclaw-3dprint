"""Bambu printer MQTT client — optional direct LAN control and monitoring."""

from __future__ import annotations

import json
import logging
import ssl
import time
from dataclasses import dataclass
from typing import Callable, Optional

log = logging.getLogger(__name__)


@dataclass
class PrintStatus:
    progress: float = 0.0  # 0-100
    layer: int = 0
    total_layers: int = 0
    remaining_time_min: int = 0
    state: str = "unknown"  # IDLE, RUNNING, PAUSE, FINISH, FAILED
    error: str = ""


class BambuMQTT:
    """MQTT client for Bambu Lab printers (LAN mode).

    Bambu printers expose an MQTT broker on port 8883 (TLS).
    Topic: device/{serial}/report  — printer publishes status
    Topic: device/{serial}/request — we publish commands
    """

    def __init__(
        self,
        printer_ip: str,
        serial: str,
        access_code: str,
        on_status: Optional[Callable[[PrintStatus], None]] = None,
    ):
        self.printer_ip = printer_ip
        self.serial = serial
        self.access_code = access_code
        self.on_status = on_status
        self._client = None
        self._latest_status = PrintStatus()

    def connect(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise ImportError("paho-mqtt is required for Bambu MQTT support")

        self._client = mqtt.Client(
            client_id=f"openclaw_{int(time.time())}",
            protocol=mqtt.MQTTv311,
        )
        self._client.username_pw_set("bblp", self.access_code)
        self._client.tls_set(cert_reqs=ssl.CERT_NONE)
        self._client.tls_insecure_set(True)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

        log.info("Connecting to Bambu printer at %s:8883 …", self.printer_ip)
        self._client.connect(self.printer_ip, 8883, keepalive=60)
        self._client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info("Connected to Bambu printer MQTT")
            client.subscribe(f"device/{self.serial}/report")
        else:
            log.error("Bambu MQTT connect failed: rc=%d", rc)

    def _on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload)
            print_data = data.get("print", {})

            self._latest_status = PrintStatus(
                progress=float(print_data.get("mc_percent", 0)),
                layer=int(print_data.get("layer_num", 0)),
                total_layers=int(print_data.get("total_layer_num", 0)),
                remaining_time_min=int(print_data.get("mc_remaining_time", 0)),
                state=print_data.get("gcode_state", "unknown"),
                error=print_data.get("error", ""),
            )

            if self.on_status:
                self.on_status(self._latest_status)
        except (json.JSONDecodeError, KeyError) as e:
            log.debug("Ignoring MQTT message parse error: %s", e)

    @property
    def status(self) -> PrintStatus:
        return self._latest_status

    def send_print_command(self, filename: str) -> None:
        """Send a print command for a file already on the printer/SD card."""
        if not self._client:
            raise RuntimeError("Not connected")

        payload = {
            "print": {
                "command": "project_file",
                "param": "Metadata/plate_1.gcode",
                "subtask_name": filename,
                "url": f"ftp://{filename}",
                "sequence_id": str(int(time.time())),
            }
        }
        self._client.publish(
            f"device/{self.serial}/request",
            json.dumps(payload),
        )
        log.info("Sent print command for %s", filename)

    def disconnect(self) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None

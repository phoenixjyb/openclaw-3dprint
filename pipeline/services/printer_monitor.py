"""Persistent printer monitor — watches Bambu P2S via MQTT and sends notifications.

Runs as a background task alongside the pipeline. Detects state transitions
(RUNNING→FINISH, errors, HMS alerts) and sends rich Telegram/Feishu messages
for ALL prints — whether triggered by our pipeline or started manually.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from dataclasses import dataclass, field
from typing import Callable, Coroutine, Optional

log = logging.getLogger(__name__)

# ── HMS error codes (common Bambu Lab codes) ──────────────────────────

HMS_CODES: dict[int, str] = {
    # AMS
    65537: "AMS filament run out",
    65538: "AMS filament broken or missing",
    65539: "AMS slot not loaded",
    65540: "AMS retry load",
    131073: "AMS communication error",
    131074: "AMS slot motor error",
    196609: "AMS humidity warning",
    # Print quality
    131156: "Nozzle or hotend needs maintenance (clean or replace)",
    196608: "Spaghetti detected — potential print failure",
    262144: "Nozzle clog detected",
    327680: "Bed adhesion lost",
    # Hardware
    131072: "Heatbed temperature error",
    196612: "Nozzle temperature error",
    262148: "Motor driver error",
    327684: "Chamber temperature too high",
    # Generic
    0: "Unknown alert",
}

# Bambu state machine
GCODE_STATES = {
    "IDLE": "💤 Idle",
    "RUNNING": "🖨️ Printing",
    "PAUSE": "⏸️ Paused",
    "FINISH": "✅ Finished",
    "FAILED": "❌ Failed",
    "PREPARE": "🔄 Preparing",
    "SLICING": "🔪 Slicing",
    "UNKNOWN": "❓ Unknown",
}


@dataclass
class PrinterSnapshot:
    """Rich printer status parsed from MQTT report."""

    state: str = "unknown"
    progress: float = 0.0
    layer: int = 0
    total_layers: int = 0
    remaining_time_min: int = 0

    # Job info
    subtask_name: str = ""
    gcode_file: str = ""
    job_id: str = ""

    # Temperatures
    nozzle_temp: float = 0.0
    nozzle_target: float = 0.0
    bed_temp: float = 0.0
    bed_target: float = 0.0

    # Speed
    speed_level: int = 0
    speed_magnitude: int = 100

    # Fans
    cooling_fan: str = "0"
    aux_fan: str = "0"
    chamber_fan: str = "0"

    # AMS trays: list of (slot, type, sub_brand, color_hex, remain_pct)
    ams_trays: list[tuple[int, str, str, str, int]] = field(default_factory=list)

    # HMS alerts: list of (attr, code)
    hms_alerts: list[tuple[int, int]] = field(default_factory=list)

    # Errors
    error_code: str = "0"
    print_error: int = 0

    # WiFi
    wifi_signal: str = ""

    # Camera
    timelapse: str = "disable"

    @property
    def job_name(self) -> str:
        return self.subtask_name or self.gcode_file.split("/")[-1] or "Unknown job"

    @property
    def state_emoji(self) -> str:
        return GCODE_STATES.get(self.state.upper(), f"❓ {self.state}")

    def format_hms(self) -> str:
        if not self.hms_alerts:
            return ""
        lines = []
        for attr, code in self.hms_alerts:
            desc = HMS_CODES.get(code, f"Alert code {code} (attr {attr})")
            lines.append(f"⚠️ {desc}")
        return "\n".join(lines)

    def format_ams(self) -> str:
        if not self.ams_trays:
            return ""
        lines = []
        for slot, ftype, brand, color, remain in self.ams_trays:
            label = f"{brand} {ftype}" if brand else ftype
            bar = _progress_bar(remain, 10)
            lines.append(f"  Slot {slot + 1}: {label} {bar} {remain}%")
        return "\n".join(lines)

    def format_temps(self) -> str:
        parts = [f"Nozzle {self.nozzle_temp:.0f}°C"]
        if self.nozzle_target > 0:
            parts[0] += f"→{self.nozzle_target:.0f}°C"
        parts.append(f"Bed {self.bed_temp:.0f}°C")
        if self.bed_target > 0:
            parts[-1] += f"→{self.bed_target:.0f}°C"
        return " | ".join(parts)


def _progress_bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    return "▓" * filled + "░" * (width - filled)


def _parse_snapshot(data: dict) -> PrinterSnapshot | None:
    """Parse a full MQTT 'print' payload into a PrinterSnapshot."""
    p = data.get("print")
    if not p:
        return None

    snap = PrinterSnapshot(
        state=p.get("gcode_state", "unknown"),
        progress=float(p.get("mc_percent", 0)),
        layer=int(p.get("layer_num", 0)),
        total_layers=int(p.get("total_layer_num", 0)),
        remaining_time_min=int(p.get("mc_remaining_time", 0)),
        subtask_name=p.get("subtask_name", ""),
        gcode_file=p.get("gcode_file", ""),
        job_id=p.get("job_id", ""),
        nozzle_temp=float(p.get("nozzle_temper", 0)),
        nozzle_target=float(p.get("nozzle_target_temper", 0)),
        bed_temp=float(p.get("bed_temper", 0)),
        bed_target=float(p.get("bed_target_temper", 0)),
        speed_level=int(p.get("spd_lvl", 0)),
        speed_magnitude=int(p.get("spd_mag", 100)),
        cooling_fan=str(p.get("cooling_fan_speed", "0")),
        aux_fan=str(p.get("big_fan1_speed", "0")),
        chamber_fan=str(p.get("big_fan2_speed", "0")),
        error_code=str(p.get("mc_print_error_code", "0")),
        print_error=int(p.get("print_error", 0)),
        wifi_signal=p.get("wifi_signal", ""),
        timelapse=(
            p.get("ipcam", {}).get("timelapse", "disable")
            if isinstance(p.get("ipcam"), dict) else "disable"
        ),
    )

    # Parse AMS trays
    ams_data = p.get("ams", {})
    if isinstance(ams_data, dict):
        for unit in ams_data.get("ams", []):
            for tray in unit.get("tray", []):
                snap.ams_trays.append((
                    int(tray.get("id", 0)),
                    tray.get("tray_type", "?"),
                    tray.get("tray_sub_brands", ""),
                    tray.get("tray_color", "000000FF"),
                    int(tray.get("remain", 0)),
                ))

    # Parse HMS alerts
    for alert in p.get("hms", []):
        snap.hms_alerts.append((
            int(alert.get("attr", 0)),
            int(alert.get("code", 0)),
        ))

    return snap


# ── Notification message builders ─────────────────────────────────

def _msg_print_started(snap: PrinterSnapshot) -> str:
    msg = (
        f"🖨️ *Print Started*\n"
        f"📄 {snap.job_name}\n"
        f"📊 {snap.total_layers} layers\n"
        f"🌡️ {snap.format_temps()}"
    )
    if snap.remaining_time_min > 0:
        h, m = divmod(snap.remaining_time_min, 60)
        msg += f"\n⏱️ Est. {h}h {m}m" if h else f"\n⏱️ Est. {m} min"
    ams = snap.format_ams()
    if ams:
        msg += f"\n🎨 AMS:\n{ams}"
    return msg


def _msg_progress(snap: PrinterSnapshot) -> str:
    bar = _progress_bar(snap.progress)
    msg = (
        f"🔄 *Printing: {snap.progress:.0f}%*\n"
        f"{bar}\n"
        f"📄 {snap.job_name}\n"
        f"📊 Layer {snap.layer}/{snap.total_layers}\n"
        f"🌡️ {snap.format_temps()}"
    )
    if snap.remaining_time_min > 0:
        h, m = divmod(snap.remaining_time_min, 60)
        msg += f"\n⏱️ Remaining: {h}h {m}m" if h else f"\n⏱️ Remaining: {m} min"
    return msg


def _msg_print_finished(snap: PrinterSnapshot) -> str:
    return (
        f"✅ *Print Complete!*\n"
        f"📄 {snap.job_name}\n"
        f"📊 {snap.total_layers} layers\n"
        f"🌡️ {snap.format_temps()}"
    )


def _msg_print_failed(snap: PrinterSnapshot) -> str:
    msg = (
        f"❌ *Print Failed!*\n"
        f"📄 {snap.job_name}\n"
        f"📊 Layer {snap.layer}/{snap.total_layers} ({snap.progress:.0f}%)\n"
        f"🌡️ {snap.format_temps()}"
    )
    if snap.error_code != "0":
        msg += f"\n🔧 Error code: {snap.error_code}"
    hms = snap.format_hms()
    if hms:
        msg += f"\n{hms}"
    return msg


def _msg_print_paused(snap: PrinterSnapshot) -> str:
    return (
        f"⏸️ *Print Paused*\n"
        f"📄 {snap.job_name}\n"
        f"📊 Layer {snap.layer}/{snap.total_layers} ({snap.progress:.0f}%)\n"
        f"🌡️ {snap.format_temps()}"
    )


def _msg_hms_alert(snap: PrinterSnapshot) -> str:
    return (
        f"🔔 *Printer Alert*\n"
        f"{snap.format_hms()}\n"
        f"🌡️ {snap.format_temps()}"
    )


# ── The monitor itself ────────────────────────────────────────────

NotifyFn = Callable[[int, str], Coroutine]


class PrinterMonitor:
    """Persistent MQTT listener that detects printer events and sends notifications.

    Args:
        printer_ip: Bambu printer LAN IP
        serial: Printer serial number
        access_code: Printer access code
        notify_chat_id: Telegram chat_id to send notifications to
        send_message: async fn(chat_id, text) for sending messages
        progress_interval: notify every N% (0 to disable progress updates)
    """

    def __init__(
        self,
        printer_ip: str,
        serial: str,
        access_code: str,
        notify_chat_id: int,
        send_message: NotifyFn,
        progress_interval: int = 25,
        mqtt_proxy_port: int = 0,
    ):
        self.printer_ip = printer_ip
        self.serial = serial
        self.access_code = access_code
        self.chat_id = notify_chat_id
        self.send_message = send_message
        self.progress_interval = progress_interval
        self.mqtt_proxy_port = mqtt_proxy_port

        self._client = None
        self._prev_state: str = ""
        self._prev_job_id: str = ""
        self._last_notified_pct: int = -1
        self._prev_hms: set[int] = set()
        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._latest_snapshot: Optional[PrinterSnapshot] = None

    @property
    def snapshot(self) -> PrinterSnapshot | None:
        return self._latest_snapshot

    async def start(self) -> None:
        """Connect to printer MQTT and begin monitoring."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.error("paho-mqtt not installed — printer monitor disabled")
            return

        self._loop = asyncio.get_event_loop()
        self._running = True

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"monitor_{int(time.time())}",
        )
        self._client.username_pw_set("bblp", self.access_code)
        self._client.tls_set(cert_reqs=ssl.CERT_NONE)
        self._client.tls_insecure_set(True)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._client.reconnect_delay_set(min_delay=5, max_delay=120)

        # Determine connection target (proxy or direct)
        if self.mqtt_proxy_port:
            self._mqtt_host = "127.0.0.1"
            self._mqtt_port = self.mqtt_proxy_port
            log.info(
                "Printer monitor connecting via proxy 127.0.0.1:%d → %s:8883 …",
                self.mqtt_proxy_port, self.printer_ip,
            )
        else:
            self._mqtt_host = self.printer_ip
            self._mqtt_port = 8883
            log.info("Printer monitor connecting to %s:8883 …", self.printer_ip)

        self._client.loop_start()
        # Try initial connection — if printer is asleep, retry in background
        asyncio.ensure_future(self._connect_with_retry())

    async def _connect_with_retry(self, max_attempts: int = 0) -> None:
        """Try to connect, retrying on failure (printer may be asleep)."""
        attempt = 0
        while self._running:
            attempt += 1
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._client.connect(
                        self._mqtt_host, self._mqtt_port, keepalive=60
                    ),
                )
                log.info("Printer monitor MQTT connection initiated")
                return
            except Exception as e:
                delay = min(30 * attempt, 120)
                log.warning(
                    "Printer monitor connect attempt %d failed: %s "
                    "(retry in %ds)",
                    attempt, e, delay,
                )
                if max_attempts and attempt >= max_attempts:
                    log.error("Printer monitor giving up after %d attempts", attempt)
                    return
                await asyncio.sleep(delay)

    async def stop(self) -> None:
        self._running = False
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        log.info("Printer monitor stopped")

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            log.info("Printer monitor connected to MQTT")
            client.subscribe(f"device/{self.serial}/report")
            # Request a full status push
            req = {"pushing": {"sequence_id": "0", "command": "pushall"}}
            client.publish(f"device/{self.serial}/request", json.dumps(req))
        else:
            log.error("Printer monitor MQTT connect failed: rc=%d", rc)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        if self._running:
            log.warning("Printer monitor disconnected (rc=%d), will auto-reconnect", rc)

    def _on_message(self, client, userdata, msg):
        """Handle MQTT message — runs in paho's thread, schedule async work."""
        try:
            data = json.loads(msg.payload)
        except json.JSONDecodeError:
            return

        snap = _parse_snapshot(data)
        if not snap:
            return

        self._latest_snapshot = snap

        if self._loop and self._running:
            asyncio.run_coroutine_threadsafe(
                self._process_snapshot(snap), self._loop
            )

    async def _process_snapshot(self, snap: PrinterSnapshot) -> None:
        """Detect state transitions and send notifications."""
        state = snap.state.upper()
        prev = self._prev_state.upper()

        try:
            # New print started
            if state == "RUNNING" and prev != "RUNNING":
                if snap.job_id != self._prev_job_id or prev in ("", "FINISH", "FAILED", "IDLE"):
                    self._prev_job_id = snap.job_id
                    self._last_notified_pct = 0
                    await self.send_message(self.chat_id, _msg_print_started(snap))

            # Progress update
            if state == "RUNNING" and self.progress_interval > 0:
                threshold = self.progress_interval
                current_bracket = int(snap.progress // threshold) * threshold
                if current_bracket > self._last_notified_pct and current_bracket < 100:
                    self._last_notified_pct = current_bracket
                    await self.send_message(self.chat_id, _msg_progress(snap))

            # Print finished
            if state == "FINISH" and prev == "RUNNING":
                await self.send_message(self.chat_id, _msg_print_finished(snap))
                self._last_notified_pct = -1

            # Print failed
            if state == "FAILED" and prev not in ("FAILED", ""):
                await self.send_message(self.chat_id, _msg_print_failed(snap))
                self._last_notified_pct = -1

            # Print paused
            if state == "PAUSE" and prev == "RUNNING":
                await self.send_message(self.chat_id, _msg_print_paused(snap))

            # New HMS alerts
            current_hms = {code for _, code in snap.hms_alerts}
            new_alerts = current_hms - self._prev_hms
            if new_alerts and state != "":
                self._prev_hms = current_hms
                await self.send_message(self.chat_id, _msg_hms_alert(snap))

        except Exception:
            log.exception("Monitor notification error")

        self._prev_state = state

    async def request_status(self) -> PrinterSnapshot | None:
        """Request a fresh status push and return the latest snapshot."""
        if self._client:
            req = {"pushing": {"sequence_id": str(int(time.time())), "command": "pushall"}}
            self._client.publish(
                f"device/{self.serial}/request", json.dumps(req)
            )
            await asyncio.sleep(2)  # give printer time to respond
        return self._latest_snapshot

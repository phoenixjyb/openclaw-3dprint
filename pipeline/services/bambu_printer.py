"""Bambu printer direct communication — FTPS upload + MQTT print control.

Enables sending .3mf files directly to a Bambu Lab printer over LAN,
without requiring Bambu Studio or a Windows PC.
"""

from __future__ import annotations

import asyncio
import ftplib
import json
import logging
import ssl
import time
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)


async def upload_file_ftp(
    printer_ip: str,
    access_code: str,
    local_path: str,
    ftp_proxy_url: str = "",
) -> str:
    """Upload a .3mf file to the printer via implicit FTPS (port 990).

    If ftp_proxy_url is set (e.g. "http://127.0.0.1:18990"), the upload
    is delegated to the FTP upload proxy service (system Python, bypasses
    macOS LNP restrictions).  Otherwise, connects directly via FTPS.

    Returns the remote filename (basename) on success.
    """
    filename = Path(local_path).name

    if ftp_proxy_url:
        return await _upload_via_proxy(ftp_proxy_url, local_path, filename,
                                       printer_ip, access_code)

    def _upload():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        ftp = ftplib.FTP_TLS(context=ctx)
        ftp.connect(printer_ip, 990, timeout=30)
        ftp.login("bblp", access_code)
        ftp.prot_p()

        # X1C has /cache; P2S uses USB root /
        try:
            ftp.cwd("/cache")
            remote_path = f"/cache/{filename}"
        except ftplib.error_perm:
            remote_path = f"/{filename}"

        log.info("FTPS uploading %s → %s", local_path, remote_path)
        with open(local_path, "rb") as f:
            ftp.storbinary(f"STOR {remote_path}", f)

        ftp.quit()
        log.info("FTPS upload complete: %s", filename)
        return filename

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _upload)


async def _upload_via_proxy(proxy_url: str, filepath: str, filename: str,
                            printer_ip: str, access_code: str) -> str:
    """Delegate FTP upload to the localhost proxy service."""
    import urllib.request

    payload = json.dumps({
        "filepath": filepath,
        "filename": filename,
        "printer_ip": printer_ip,
        "access_code": access_code,
    }).encode()

    url = proxy_url.rstrip("/") + "/upload"
    req = urllib.request.Request(url, data=payload,
                                headers={"Content-Type": "application/json"})

    def _do():
        resp = urllib.request.urlopen(req, timeout=120)
        body = json.loads(resp.read())
        if not body.get("ok"):
            raise RuntimeError(f"FTP proxy error: {body}")
        log.info("FTP proxy upload complete: %s", body.get("filename"))
        return body["filename"]

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _do)


async def start_print_mqtt(
    printer_ip: str,
    access_code: str,
    serial: str,
    filename: str,
    mqtt_proxy_port: int = 0,
) -> None:
    """Send a print-start command via MQTT (port 8883, TLS).

    If mqtt_proxy_port > 0, connect to localhost:mqtt_proxy_port (plain TCP)
    instead of printer_ip:8883 (TLS).  The proxy handles TLS termination.
    """
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        raise ImportError("paho-mqtt is required: pip install paho-mqtt")

    connected = asyncio.Event()
    error_msg: list[str] = []

    use_proxy = mqtt_proxy_port > 0
    host = "127.0.0.1" if use_proxy else printer_ip
    port = mqtt_proxy_port if use_proxy else 8883

    def _send():
        client = mqtt.Client(
            client_id=f"openclaw_{int(time.time())}",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set("bblp", access_code)
        if not use_proxy:
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)

        def on_connect(c, userdata, flags, rc):
            if rc != 0:
                error_msg.append(f"MQTT connect failed: rc={rc}")
                connected.set()
                return

            payload = {
                "print": {
                    "command": "project_file",
                    "param": "Metadata/plate_1.gcode",
                    "subtask_name": filename,
                    "url": f"file:///sdcard/{filename}",
                    "file": "",
                    "md5": "",
                    "timelapse": False,
                    "bed_type": "auto",
                    "bed_levelling": True,
                    "flow_cali": True,
                    "vibration_cali": True,
                    "layer_inspect": False,
                    "use_ams": False,
                    "ams_mapping": [],
                    "project_id": "0",
                    "profile_id": "0",
                    "task_id": "0",
                    "subtask_id": "0",
                    "sequence_id": str(int(time.time())),
                }
            }
            topic = f"device/{serial}/request"
            c.publish(topic, json.dumps(payload))
            log.info("Sent print command for %s on topic %s", filename, topic)
            connected.set()

        client.on_connect = on_connect
        client.connect(host, port, keepalive=60)
        client.loop_start()

        # Wait for connection + publish
        import threading
        evt = threading.Event()

        orig_set = connected.set  # noqa: F841

        def _notify():
            evt.set()
        connected.set = _notify

        evt.wait(timeout=15)
        client.loop_stop()
        client.disconnect()

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send)

    if error_msg:
        raise RuntimeError(error_msg[0])


async def monitor_print_mqtt(
    printer_ip: str,
    access_code: str,
    serial: str,
    progress_callback: Optional[Callable] = None,
    poll_interval: int = 30,
    timeout: int = 86400,
) -> None:
    """Subscribe to printer reports via MQTT and monitor until print completes."""
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        raise ImportError("paho-mqtt is required: pip install paho-mqtt")

    from pipeline.services.bambu_mqtt import PrintStatus

    latest_status = {"value": PrintStatus()}
    error_holder: list[str] = []

    def _monitor():
        client = mqtt.Client(
            client_id=f"openclaw_mon_{int(time.time())}",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set("bblp", access_code)
        client.tls_set(cert_reqs=ssl.CERT_NONE)
        client.tls_insecure_set(True)

        def on_connect(c, userdata, flags, rc):
            if rc == 0:
                c.subscribe(f"device/{serial}/report")
                log.info("Monitoring printer %s via MQTT", serial)
            else:
                error_holder.append(f"MQTT monitor connect failed: rc={rc}")

        def on_message(c, userdata, msg):
            try:
                data = json.loads(msg.payload)
                print_data = data.get("print", {})
                status = PrintStatus(
                    progress=float(print_data.get("mc_percent", 0)),
                    layer=int(print_data.get("layer_num", 0)),
                    total_layers=int(print_data.get("total_layer_num", 0)),
                    remaining_time_min=int(print_data.get("mc_remaining_time", 0)),
                    state=print_data.get("gcode_state", "unknown"),
                    error=print_data.get("error", ""),
                )
                latest_status["value"] = status

                if status.state == "FINISH":
                    log.info("Print finished (MQTT monitor)")
                elif status.state == "FAILED":
                    error_holder.append(f"Print failed: {status.error}")
            except (json.JSONDecodeError, KeyError):
                pass

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(printer_ip, 8883, keepalive=60)
        client.loop_start()
        return client

    loop = asyncio.get_event_loop()
    mqtt_client = await loop.run_in_executor(None, _monitor)

    try:
        elapsed = 0
        last_reported_pct = -1

        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            status = latest_status["value"]
            current_pct = int(status.progress // 10) * 10
            if current_pct != last_reported_pct and progress_callback:
                await progress_callback(status)
                last_reported_pct = current_pct

            if error_holder:
                raise RuntimeError(error_holder[0])

            if status.state == "FINISH":
                if progress_callback:
                    await progress_callback(status)
                return
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

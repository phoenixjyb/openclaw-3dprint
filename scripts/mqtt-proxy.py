#!/usr/bin/env /usr/bin/python3
"""TCP proxy: localhost:LOCAL_PORT → PRINTER_IP:8883

Works around macOS Local Network Privacy restrictions that block
Homebrew-installed Python from reaching LAN devices.  This script
runs under /usr/bin/python3 (Apple-signed, exempt from the restriction)
and simply relays bytes in both directions.

Usage
-----
    /usr/bin/python3 scripts/mqtt-proxy.py                 # defaults
    PRINTER_IP=192.168.0.102 LOCAL_PORT=18883 /usr/bin/python3 scripts/mqtt-proxy.py

The pipeline's printer-monitor connects to localhost:18883 instead of
the printer IP directly.
"""

import os
import select
import signal
import socket
import sys
import threading

PRINTER_IP = os.environ.get("PRINTER_IP", os.environ.get("BAMBU_PRINTER_IP", "192.168.0.102"))
PRINTER_PORT = int(os.environ.get("PRINTER_PORT", "8883"))
LOCAL_PORT = int(os.environ.get("LOCAL_PORT", "18883"))
BUFSIZE = 8192

_shutdown = threading.Event()


def _relay(label: str, src: socket.socket, dst: socket.socket) -> None:
    """Forward bytes from *src* to *dst* until either side closes."""
    try:
        while not _shutdown.is_set():
            ready, _, _ = select.select([src], [], [], 1.0)
            if ready:
                data = src.recv(BUFSIZE)
                if not data:
                    break
                dst.sendall(data)
    except (OSError, BrokenPipeError):
        pass
    finally:
        try:
            src.close()
        except OSError:
            pass
        try:
            dst.close()
        except OSError:
            pass


def _handle_client(client_sock: socket.socket, addr: tuple) -> None:
    """Connect to the printer and relay traffic for one client.

    This is a transparent TCP proxy — bytes pass through unmodified.
    paho-mqtt handles TLS end-to-end through the tunnel.
    """
    print(f"[mqtt-proxy] new client {addr} → {PRINTER_IP}:{PRINTER_PORT}")
    try:
        remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        remote.settimeout(10)
        remote.connect((PRINTER_IP, PRINTER_PORT))
        remote.settimeout(None)
    except Exception as exc:
        print(f"[mqtt-proxy] failed to reach printer: {exc}")
        client_sock.close()
        return

    t1 = threading.Thread(target=_relay, args=("c→p", client_sock, remote), daemon=True)
    t2 = threading.Thread(target=_relay, args=("p→c", remote, client_sock), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    print(f"[mqtt-proxy] session closed for {addr}")


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: _shutdown.set())
    signal.signal(signal.SIGINT, lambda *_: _shutdown.set())

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", LOCAL_PORT))
    srv.listen(4)
    srv.settimeout(1.0)
    print(f"[mqtt-proxy] listening on 127.0.0.1:{LOCAL_PORT} → {PRINTER_IP}:{PRINTER_PORT}")

    while not _shutdown.is_set():
        try:
            client, addr = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=_handle_client, args=(client, addr), daemon=True).start()

    srv.close()
    print("[mqtt-proxy] stopped")


if __name__ == "__main__":
    main()

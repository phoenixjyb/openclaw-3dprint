#!/usr/bin/env /usr/bin/python3
"""TLS-terminating TCP proxy: localhost:LOCAL_PORT (plain) → PRINTER_IP:8883 (TLS)

Works around macOS Local Network Privacy + SSL restrictions that block
Homebrew-installed Python from reaching LAN devices.  This script
runs under /usr/bin/python3 (Apple-signed, exempt from the restriction)
and terminates TLS — clients connect with plain TCP.

Usage
-----
    /usr/bin/python3 scripts/mqtt-proxy.py
    PRINTER_IP=192.168.0.102 LOCAL_PORT=18883 /usr/bin/python3 scripts/mqtt-proxy.py

The pipeline's printer-monitor connects to localhost:18883 with TLS disabled.
"""

import os
import select
import signal
import socket
import ssl
import threading

PRINTER_IP = os.environ.get("PRINTER_IP", os.environ.get("BAMBU_PRINTER_IP", "192.168.0.102"))
PRINTER_PORT = int(os.environ.get("PRINTER_PORT", "8883"))
LOCAL_PORT = int(os.environ.get("LOCAL_PORT", "18883"))
BUFSIZE = 8192

def _relay(label, src, dst):
    """Forward bytes from *src* to *dst* until either side closes."""
    try:
        while True:
            ready, _, _ = select.select([src], [], [], 2.0)
            if ready:
                data = src.recv(BUFSIZE)
                if not data:
                    break
                dst.sendall(data)
    except (OSError, BrokenPipeError):
        pass
    try:
        src.close()
    except OSError:
        pass
    try:
        dst.close()
    except OSError:
        pass


def _handle_client(client_sock, addr):
    """Connect to the printer (TLS) and relay decrypted traffic for one client.

    Client side: plain TCP (no TLS).
    Printer side: TLS (terminated by this proxy).
    """
    print(f"[mqtt-proxy] new client {addr} → {PRINTER_IP}:{PRINTER_PORT}", flush=True)
    try:
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(10)
        raw.connect((PRINTER_IP, PRINTER_PORT))

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        remote = ctx.wrap_socket(raw)
        remote.settimeout(None)
        print("[mqtt-proxy] TLS connected to printer", flush=True)
    except Exception as exc:
        print(f"[mqtt-proxy] failed to reach printer: {exc}", flush=True)
        client_sock.close()
        return

    t1 = threading.Thread(target=_relay, args=("c→p", client_sock, remote), daemon=True)
    t2 = threading.Thread(target=_relay, args=("p→c", remote, client_sock), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    print(f"[mqtt-proxy] session closed for {addr}", flush=True)


def main():
    def _stop(sig, frame):
        print("[mqtt-proxy] stopping", flush=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", LOCAL_PORT))
    srv.listen(4)
    srv.settimeout(1.0)
    print(f"[mqtt-proxy] listening on 127.0.0.1:{LOCAL_PORT} → {PRINTER_IP}:{PRINTER_PORT}", flush=True)

    while True:
        try:
            client, addr = srv.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=_handle_client, args=(client, addr), daemon=True).start()

    srv.close()
    print("[mqtt-proxy] stopped", flush=True)


if __name__ == "__main__":
    main()

#!/usr/bin/env /usr/bin/python3
"""HTTP micro-service that accepts file uploads and sends them to a Bambu printer via FTPS.

Works around macOS Local Network Privacy — Homebrew Python cannot reach
LAN devices, but /usr/bin/python3 (Apple-signed) can.  The pipeline
POSTs files to this localhost service, which relays them over implicit
FTPS to the printer.

Usage
-----
    /usr/bin/python3 scripts/ftp-upload-proxy.py
    PRINTER_IP=192.168.0.102 ACCESS_CODE=12345678 LOCAL_PORT=18990 /usr/bin/python3 scripts/ftp-upload-proxy.py

API
---
    POST /upload
        Form fields: file (multipart), printer_ip (optional), access_code (optional)
        Returns: {"ok": true, "filename": "..."}

    GET /health
        Returns: {"status": "ok"}
"""

import ftplib
import http.server
import io
import json
import os
import signal
import socket
import ssl
import tempfile
import traceback

PRINTER_IP = os.environ.get("PRINTER_IP", os.environ.get("BAMBU_PRINTER_IP", "192.168.0.102"))
ACCESS_CODE = os.environ.get("ACCESS_CODE", os.environ.get("BAMBU_PRINTER_ACCESS_CODE", ""))
LOCAL_PORT = int(os.environ.get("LOCAL_PORT", "18990"))


class ImplicitFTPS(ftplib.FTP_TLS):
    """FTP_TLS subclass supporting implicit FTPS with TLS session reuse.

    Bambu printers (vsFTPd) require:
    1. Implicit TLS (TLS from connection start, not STARTTLS)
    2. TLS session reuse on data channels (522 error without it)
    """

    def __init__(self, host, port, access_code, timeout=30):
        self._host = host
        self._port = port
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE
        super().__init__(context=self._ctx)

        # Implicit TLS: wrap socket before any FTP protocol exchange
        raw = socket.create_connection((host, port), timeout=timeout)
        self.sock = self._ctx.wrap_socket(raw, server_hostname=host)
        self.af = socket.AF_INET
        self.host = host
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()

    def ntransfercmd(self, cmd, rest=None):
        """Override to reuse the control channel's TLS session on data channels."""
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            session = self.sock.session  # reuse control channel session
            conn = self._ctx.wrap_socket(
                conn, server_hostname=self._host, session=session,
            )
        return conn, size


def _ftps_upload(printer_ip, access_code, filepath, remote_filename):
    """Upload a file to the Bambu printer via implicit FTPS (port 990)."""
    ftp = ImplicitFTPS(printer_ip, 990, access_code)
    print(f"[ftp-proxy] connected to {printer_ip}:990 — {ftp.welcome}", flush=True)

    ftp.login("bblp", access_code)
    ftp.prot_p()

    # P2S stores on USB at root; X1C uses /cache. Try /cache first, fall back to root.
    remote_path = f"/cache/{remote_filename}"
    try:
        ftp.cwd("/cache")
    except ftplib.error_perm:
        remote_path = f"/{remote_filename}"

    print(f"[ftp-proxy] uploading {filepath} → {remote_path}", flush=True)
    with open(filepath, "rb") as f:
        ftp.storbinary(f"STOR {remote_path}", f)

    ftp.quit()
    print(f"[ftp-proxy] upload complete: {remote_filename}", flush=True)
    return remote_filename


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[ftp-proxy] {fmt % args}", flush=True)

    def do_GET(self):
        if self.path == "/health":
            self._json_response(200, {"status": "ok"})
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/upload":
            self._json_response(404, {"error": "not found"})
            return

        content_type = self.headers.get("Content-Type", "")

        if "multipart/form-data" in content_type:
            self._handle_multipart()
        elif "application/json" in content_type:
            self._handle_json()
        else:
            self._json_response(400, {"error": f"unsupported content type: {content_type}"})

    def _handle_json(self):
        """Accept JSON with local file path — proxy reads the file and uploads."""
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        filepath = body.get("filepath", "")
        filename = body.get("filename", "")
        printer_ip = body.get("printer_ip", PRINTER_IP)
        access_code = body.get("access_code", ACCESS_CODE)

        if not filepath or not os.path.isfile(filepath):
            self._json_response(400, {"error": f"file not found: {filepath}"})
            return
        if not filename:
            filename = os.path.basename(filepath)
        if not access_code:
            self._json_response(400, {"error": "no access_code provided"})
            return

        try:
            _ftps_upload(printer_ip, access_code, filepath, filename)
            self._json_response(200, {"ok": True, "filename": filename})
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": str(e)})

    def _handle_multipart(self):
        """Accept multipart file upload."""
        import cgi
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST"},
        )

        file_item = form["file"] if "file" in form else None
        if not file_item or not file_item.file:
            self._json_response(400, {"error": "missing 'file' field"})
            return

        filename = file_item.filename or "model.3mf"
        printer_ip = form.getvalue("printer_ip", PRINTER_IP)
        access_code = form.getvalue("access_code", ACCESS_CODE)

        if not access_code:
            self._json_response(400, {"error": "no access_code provided"})
            return

        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as tmp:
            tmp.write(file_item.file.read())
            tmp_path = tmp.name

        try:
            _ftps_upload(printer_ip, access_code, tmp_path, filename)
            self._json_response(200, {"ok": True, "filename": filename})
        except Exception as e:
            traceback.print_exc()
            self._json_response(500, {"error": str(e)})
        finally:
            os.unlink(tmp_path)

    def _json_response(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    def _stop(sig, frame):
        print("[ftp-proxy] stopping", flush=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    server = http.server.HTTPServer(("127.0.0.1", LOCAL_PORT), Handler)
    print(f"[ftp-proxy] listening on 127.0.0.1:{LOCAL_PORT} "
          f"(printer={PRINTER_IP})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

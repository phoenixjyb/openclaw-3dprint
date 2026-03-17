"""Windows SSH/SCP client — wraps paramiko for file transfer and remote commands.

Only used when slicer_mode=remote or bambu_send_method=studio.
Requires the 'windows' optional dependency: pip install openclaw-3dprint[windows]
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


class WindowsSSH:
    """Manages SSH connections to a remote Windows PC."""

    def __init__(
        self,
        host: str,
        user: str,
        port: int = 22,
        key_path: str = "",
        connect_timeout: int = 15,
    ):
        self.host = host
        self.user = user
        self.port = port
        self.key_path = key_path
        self.connect_timeout = connect_timeout
        self._client = None

    def connect(self) -> None:
        try:
            import paramiko
        except ImportError:
            raise ImportError(
                "paramiko is required for Windows SSH: pip install openclaw-3dprint[windows]"
            )

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kwargs: dict = {
            "hostname": self.host,
            "username": self.user,
            "port": self.port,
            "timeout": self.connect_timeout,
        }
        if self.key_path:
            kwargs["key_filename"] = str(Path(self.key_path).expanduser())

        log.info("SSH connecting to %s@%s:%d …", self.user, self.host, self.port)
        self._client.connect(**kwargs)
        log.info("SSH connected.")

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def exec(self, command: str, timeout: int = 300) -> tuple[str, str, int]:
        """Execute a command, return (stdout, stderr, exit_code)."""
        if not self._client:
            self.connect()
        assert self._client is not None

        log.info("SSH exec: %s", command[:200])
        _, stdout, stderr = self._client.exec_command(command, timeout=timeout)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")

        if exit_code != 0:
            log.warning("SSH command exit %d: stderr=%s", exit_code, err[:500])
        return out, err, exit_code

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """SCP upload a file to the remote machine."""
        if not self._client:
            self.connect()
        assert self._client is not None

        sftp = self._client.open_sftp()
        try:
            log.info("SFTP upload: %s → %s", local_path, remote_path)
            sftp.put(local_path, remote_path)
            log.info("Upload complete.")
        finally:
            sftp.close()

    def download_file(self, remote_path: str, local_path: str) -> None:
        """SCP download a file from the remote machine."""
        if not self._client:
            self.connect()
        assert self._client is not None

        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        sftp = self._client.open_sftp()
        try:
            log.info("SFTP download: %s → %s", remote_path, local_path)
            sftp.get(remote_path, local_path)
            log.info("Download complete.")
        finally:
            sftp.close()

    def ensure_directory(self, remote_dir: str) -> None:
        """Create a directory on Windows if it doesn't exist."""
        cmd = f"powershell -Command \"New-Item -ItemType Directory -Force -Path '{remote_dir}'\""
        self.exec(cmd)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()

"""Feishu (Lark) Bot API client — send messages, images, and cards."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuClient:
    """Thin wrapper around Feishu Bot API for sending messages."""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token: str = ""
        self._token_expires: float = 0
        self._http = httpx.AsyncClient(timeout=30)

    async def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token

        resp = await self._http.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu token error: {data}")

        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200)
        log.info("Feishu tenant_access_token refreshed")
        return self._token

    async def _headers(self) -> dict:
        token = await self._ensure_token()
        return {"Authorization": f"Bearer {token}"}

    async def send_text(self, chat_id: str, text: str) -> str:
        """Send a text message to a chat. Returns message_id."""
        headers = await self._headers()
        resp = await self._http.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=headers,
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": f'{{"text":"{_escape_json(text)}"}}',
            },
        )
        data = resp.json()
        if data.get("code") != 0:
            log.error("Feishu send_text failed: %s", data)
            raise RuntimeError(f"Feishu send error: {data.get('msg')}")
        msg_id = data.get("data", {}).get("message_id", "")
        log.info("Feishu message sent: %s", msg_id)
        return msg_id

    async def send_rich_text(self, chat_id: str, title: str, content: str) -> str:
        """Send a rich-text (post) message."""
        import json

        headers = await self._headers()
        post_body = {
            "zh_cn": {
                "title": title,
                "content": [
                    [{"tag": "text", "text": content}],
                ],
            },
        }
        resp = await self._http.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=headers,
            json={
                "receive_id": chat_id,
                "msg_type": "post",
                "content": json.dumps(post_body),
            },
        )
        data = resp.json()
        if data.get("code") != 0:
            log.error("Feishu send_rich_text failed: %s", data)
            raise RuntimeError(f"Feishu send error: {data.get('msg')}")
        return data.get("data", {}).get("message_id", "")

    async def send_image(
        self, chat_id: str, image_path: str, caption: str = ""
    ) -> str:
        """Upload image and send it to a chat."""
        headers = await self._headers()

        path = Path(image_path)
        with open(path, "rb") as f:
            resp = await self._http.post(
                f"{FEISHU_BASE}/im/v1/images",
                headers=headers,
                data={"image_type": "message"},
                files={"image": (path.name, f, "image/png")},
            )
        data = resp.json()
        if data.get("code") != 0:
            log.error("Feishu image upload failed: %s", data)
            if caption:
                return await self.send_text(chat_id, caption)
            return ""

        image_key = data["data"]["image_key"]

        import json

        resp = await self._http.post(
            f"{FEISHU_BASE}/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers=headers,
            json={
                "receive_id": chat_id,
                "msg_type": "image",
                "content": json.dumps({"image_key": image_key}),
            },
        )
        data = resp.json()
        msg_id = data.get("data", {}).get("message_id", "")

        if caption:
            await self.send_text(chat_id, caption)

        return msg_id

    async def close(self) -> None:
        await self._http.aclose()


def _escape_json(text: str) -> str:
    """Escape text for embedding in a JSON string value."""
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )

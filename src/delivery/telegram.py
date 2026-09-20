"""TelegramBot — дайджест и алерты сторожа через Bot API.

Токен и chat id читаются из окружения или `.env` по именам из
`config.yaml → telegram` (по умолчанию TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).
Текст режется под лимит сообщения по границам блоков; 429 и 5xx повторяются с
паузой (для 429 — той, что просит Telegram), остальные ошибки — сразу
`DeliveryError`. Одинаковые подряд алерты подавляются по хэшу в `data/`.
"""

from __future__ import annotations

import hashlib
import os
import time
from typing import Callable

import httpx

from ..config import TelegramConfig
from ..utils import get_logger, load_env_secret

log = get_logger("telegram.bot")

API_BASE = "https://api.telegram.org"
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 30.0


class DeliveryError(Exception):
    """Сообщение не ушло: нет ключей, отказ API или сеть после всех попыток."""


class TelegramBot:
    def __init__(
        self,
        config: TelegramConfig,
        env_path: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.config = config
        self.env_path = env_path
        self._transport = transport
        self._sleep = sleep

    # ── credentials ────────────────────────────────────────────────────────

    def credentials(self) -> tuple[str, str]:
        return (
            load_env_secret(self.config.bot_token_env, self.env_path),
            load_env_secret(self.config.chat_id_env, self.env_path),
        )

    @property
    def configured(self) -> bool:
        token, chat_id = self.credentials()
        return bool(token and chat_id)

    def _require(self, purpose: str) -> tuple[str, str]:
        token, chat_id = self.credentials()
        if not token or not chat_id:
            raise DeliveryError(
                f"{purpose}: задайте {self.config.bot_token_env} и {self.config.chat_id_env} "
                "в окружении или .env"
            )
        return token, chat_id

    # ── sending ────────────────────────────────────────────────────────────

    def _client(self) -> httpx.Client:
        kwargs: dict = {"timeout": REQUEST_TIMEOUT}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def send_text(self, text: str, *, parse_mode: str | None = "HTML") -> int:
        """Отправить текст, при нужде несколькими сообщениями. Возвращает их число."""
        token, chat_id = self._require("доставка в Telegram")
        parts = split_message(text.strip(), self.config.message_limit)
        with self._client() as client:
            for index, part in enumerate(parts, 1):
                body = f"[{index}/{len(parts)}]\n{part}" if len(parts) > 1 else part
                self._send_one(client, token, chat_id, body, parse_mode)
        log.info("в Telegram ушло %d сообщение(й)", len(parts))
        return len(parts)

    def _send_one(
        self, client: httpx.Client, token: str, chat_id: str, text: str, parse_mode: str | None
    ) -> None:
        url = f"{API_BASE}/bot{token}/sendMessage"
        payload: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        last_error = ""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = client.post(url, json=payload)
            except httpx.RequestError as e:
                last_error = f"сеть: {e}"
                log.warning("попытка %d/%d: %s", attempt, MAX_ATTEMPTS, last_error)
                if attempt < MAX_ATTEMPTS:
                    self._sleep(2**attempt)
                continue
            if response.status_code == 200:
                return
            if response.status_code == 429 or response.status_code >= 500:
                retry_after = _retry_after(response)
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                log.warning("попытка %d/%d: %s", attempt, MAX_ATTEMPTS, last_error)
                if attempt < MAX_ATTEMPTS:
                    self._sleep(retry_after or 2**attempt)
                continue
            raise DeliveryError(
                f"Telegram отказал (HTTP {response.status_code}): {response.text[:200]}"
            )
        raise DeliveryError(f"Telegram недоступен после {MAX_ATTEMPTS} попыток: {last_error}")

    # ── alerts ─────────────────────────────────────────────────────────────

    def send_alert(self, text: str, hash_path: str) -> bool:
        """Алерт сторожа; одинаковый с предыдущим — не отправляется (False)."""
        text = text.strip()
        if not text:
            return False
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            with open(hash_path, encoding="utf-8") as handle:
                if handle.read().strip() == digest:
                    log.info("такой алерт уже отправлен — повтор подавлен")
                    return False
        except OSError:
            pass
        self.send_text(text, parse_mode=None)
        try:
            os.makedirs(os.path.dirname(hash_path) or ".", exist_ok=True)
            with open(hash_path, "w", encoding="utf-8") as handle:
                handle.write(digest)
        except OSError as e:
            log.warning("не удалось запомнить хэш алерта в %s: %s", hash_path, e)
        return True


def _retry_after(response: httpx.Response) -> float | None:
    if response.status_code != 429:
        return None
    try:
        value = response.json().get("parameters", {}).get("retry_after")
    except ValueError:
        return None
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def split_message(text: str, limit: int) -> list[str]:
    """Порезать текст под лимит Telegram по границам блоков (пустая строка), а
    блок длиннее лимита — по строкам. Текст короче лимита — одна часть."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            parts.append("\n\n".join(current))
            current, current_len = [], 0

    for block in text.split("\n\n"):
        extra = len(block) + (2 if current else 0)
        if len(block) > limit:
            flush()
            parts.extend(_split_lines(block, limit))
            continue
        if current and current_len + extra > limit:
            flush()
            extra = len(block)
        current.append(block)
        current_len += extra
    flush()
    return parts


def _split_lines(block: str, limit: int) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in block.split("\n"):
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > limit:
            parts.append("\n".join(current))
            current, current_len = [line], len(line)
        else:
            current.append(line)
            current_len += extra
    if current:
        parts.append("\n".join(current))
    return parts

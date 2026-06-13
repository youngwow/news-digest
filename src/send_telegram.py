#!/usr/bin/env python3
"""
Deliver the current digest — or a monitoring alert — to a Telegram chat.

Modes:
    send_telegram.py            deliver data/digest.json (no-op if telegram.enabled=false)
    send_telegram.py --alert    send stdin as an ops alert (no-op if telegram.alerts=false;
                                consecutive identical alerts are suppressed)

Both modes exit 0 on their no-op paths so callers (run.sh, pipeline_check.sh)
can invoke them unconditionally.

Secrets (process env or PROJECT_ROOT/.env):
    TELEGRAM_BOT_TOKEN — bot token from @BotFather
    TELEGRAM_CHAT_ID   — target chat/channel id
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

import httpx

from format_telegram import format_digest, split_message
from utils import CONFIG, DATA_DIR, get_logger, load_env_secret, load_json

DIGEST_JSON = os.path.join(DATA_DIR, "digest.json")
LAST_ALERT_HASH_PATH = os.path.join(DATA_DIR, ".last_alert_hash")
API_BASE = "https://api.telegram.org"
MAX_ATTEMPTS = 3
REQUEST_TIMEOUT = 30

log = get_logger("send_telegram")


def send_message(client: httpx.Client, token: str, chat_id: str, text: str) -> bool:
    """Send one message. Retries 429 (honoring Telegram's retry_after) and 5xx."""
    url = f"{API_BASE}/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = client.post(url, json=payload)
        except httpx.RequestError as e:
            log.warning("attempt %d/%d: request error: %s", attempt, MAX_ATTEMPTS, e)
            if attempt < MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            return True

        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = None
            if resp.status_code == 429:
                try:
                    retry_after = resp.json().get("parameters", {}).get("retry_after")
                except ValueError:
                    pass
            log.warning("attempt %d/%d: HTTP %d: %s", attempt, MAX_ATTEMPTS,
                        resp.status_code, resp.text[:200])
            if attempt < MAX_ATTEMPTS:
                time.sleep(retry_after if retry_after else 2 ** attempt)
            continue

        # Other 4xx (bad token, unknown chat, …) won't improve with retries
        log.error("HTTP %d (not retryable): %s", resp.status_code, resp.text[:200])
        return False

    log.error("all %d attempts failed", MAX_ATTEMPTS)
    return False


def deliver(digest_path: str = DIGEST_JSON) -> int:
    """Send the digest at digest_path. Returns a process exit code."""
    if not CONFIG["telegram"]["enabled"]:
        log.info("telegram.enabled is false — skipping delivery (no-op)")
        return 0

    token = load_env_secret("TELEGRAM_BOT_TOKEN")
    chat_id = load_env_secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("telegram.enabled is true but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
                  "is not set in env or .env")
        return 1

    if not os.path.exists(digest_path):
        log.error("digest not found at %s — run the pipeline first", digest_path)
        return 1

    parts = split_message(format_digest(load_json(digest_path)))
    log.info("Delivering digest in %d message(s)", len(parts))

    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for i, part in enumerate(parts, 1):
            text = f"[{i}/{len(parts)}]\n{part}" if len(parts) > 1 else part
            if not send_message(client, token, chat_id, text):
                log.error("delivery failed at part %d/%d", i, len(parts))
                return 1

    log.info("Delivered %d message(s)", len(parts))
    return 0


def send_alert(text: str, hash_path: str = LAST_ALERT_HASH_PATH) -> int:
    """Send `text` as an ops alert. Returns a process exit code.

    Suppresses a resend when the alert text is identical to the last one sent
    (hash stored at `hash_path`), so a 30-minute watchdog cron doesn't repeat
    the same message until the situation changes.
    """
    if not CONFIG["telegram"]["alerts"]:
        log.info("telegram.alerts is false — skipping alert (no-op)")
        return 0

    text = text.strip()
    if not text:
        return 0

    token = load_env_secret("TELEGRAM_BOT_TOKEN")
    chat_id = load_env_secret("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("telegram.alerts is true but TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
                  "is not set in env or .env")
        return 1

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    try:
        with open(hash_path, encoding="utf-8") as f:
            if f.read().strip() == digest:
                log.info("identical alert already sent — suppressing resend")
                return 0
    except OSError:
        pass  # no previous alert recorded

    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for part in split_message(text):
            if not send_message(client, token, chat_id, part):
                log.error("alert delivery failed")
                return 1

    try:
        with open(hash_path, "w", encoding="utf-8") as f:
            f.write(digest)
    except OSError as e:
        log.warning("could not record alert hash at %s: %s", hash_path, e)

    log.info("Alert delivered")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Send digest or ops alert to Telegram")
    parser.add_argument("--alert", action="store_true",
                        help="Read alert text from stdin and send it (gated by telegram.alerts)")
    args = parser.parse_args()

    if args.alert:
        return send_alert(sys.stdin.read())
    return deliver()


if __name__ == "__main__":
    raise SystemExit(main())

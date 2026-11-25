#!/usr/bin/env python3
"""Send an HTML-captioned build document without leaking bot credentials."""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from changelog import recent_commit_text
from presentation import (
    duration_label,
    image_label,
    linker_label,
    local_time_labels,
    toolchain_label,
    zip_size_label,
)
from publication_settings import commit_count
from runtime import run

CAPTION_LIMIT = 1024
# Hosted Telegram Bot API document limit.
DOCUMENT_LIMIT = 50_000_000


def utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def shorten(value: str, limit: int) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def commit_excerpt(
    message: str, budget: int, notice: str = "\n… [trimmed; full message in build logs]"
) -> str:
    """Bound escaped text without splitting an HTML entity or losing line breaks."""
    if utf16_length(html.escape(message)) <= budget:
        return message
    if utf16_length(html.escape(notice)) >= budget:
        raise ValueError("Insufficient caption space for the latest commit message")
    low, high = 0, len(message)
    while low < high:
        middle = (low + high + 1) // 2
        if utf16_length(html.escape(message[:middle] + notice)) <= budget:
            low = middle
        else:
            high = middle - 1
    return message[:low].rstrip() + notice


def make_caption(
    meta: dict,
    environment: dict,
    clang: str,
    lld: str,
    commit_message: str,
    history_title: str = "📝 Latest commit",
    trim_notice: str = "\n… [trimmed; full message in build logs]",
) -> str:
    # Preserve commit paragraphs while removing non-display control characters.
    message = commit_message.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    message = "".join(char for char in message if char.isprintable() or char == "\n").strip()
    if not message:
        raise ValueError("The latest commit message is empty")
    duration = duration_label(meta["duration_seconds"])
    compiler = toolchain_label(clang, environment["TOOLCHAIN"])
    linker = linker_label(lld)
    _, local_date = local_time_labels(meta)
    image = image_label(environment["IMAGE_TYPE"])
    checksum = meta.get("md5")
    if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", checksum):
        raise ValueError("MD5 hash must be exactly 32 hexadecimal characters")
    escape = html.escape
    # Measuring escaped HTML in UTF-16 conservatively bounds Telegram captions.
    for field_limit in (96, 72, 56, 40, 24, 16):

        def field(value: str, limit: int = field_limit) -> str:
            return escape(shorten(value, limit))

        prefix = (
            "<b>✅ Kernel Compiled Successfully</b>\n\n"
            f"🔩 <b>ToolChain:</b> {field(compiler)}\n"
            f"🧶 <b>Linker:</b> {field(linker)}\n"
            f"⏳ <b>Build Time:</b> {duration}\n"
            f"〽️ <b>Zip Size:</b> {zip_size_label(meta['zip_bytes'])}\n"
            f"📅 <b>Date:</b> {escape(local_date)}\n"
            f"📦 <b>Image:</b> {escape(image)}\n\n"
            f"<b>🔑 MD5 hash</b>\n<pre><code>{checksum}</code></pre>\n\n"
            f"<b>{escape(history_title)}</b>\n"
        )
        opening, closing = "<pre><code>", "</code></pre>"
        budget = CAPTION_LIMIT - utf16_length(prefix + opening + closing)
        # Reserve caption space for commit content.
        if budget < min(200, utf16_length(escape(message))):
            continue
        excerpt = commit_excerpt(message, budget, trim_notice)
        return prefix + opening + escape(excerpt) + closing
    raise ValueError("Required build metadata cannot fit Telegram's 1024-character caption limit")


def telegram_request(
    method: str, fields: dict[str, str], token: str, chat_id: str, archive: Path | None = None
) -> None:
    validate_bot_token(token)
    if not re.fullmatch(r"(?:-?[0-9]+|@[A-Za-z0-9_]+)", chat_id):
        raise ValueError("Telegram destination must be a numeric chat ID or @channel username")
    with tempfile.TemporaryDirectory(prefix="kernel-build-telegram-") as temporary:
        response_path = Path(temporary) / "response.json"
        command = [
            "curl",
            "--config",
            "-",
            "--silent",
            "--show-error",
            "--fail-with-body",
            "--connect-timeout",
            "20",
            "--max-time",
            "300",
            "--proto",
            "=https",
            "--output",
            str(response_path),
            "--form-string",
            f"chat_id={chat_id}",
            "--form-string",
            "parse_mode=HTML",
        ]
        for key, value in fields.items():
            # Keep large messages out of argv using controlled temporary filenames.
            value_path = Path(temporary) / f"{key}.txt"
            value_path.write_text(value, encoding="utf-8")
            command += ["--form", f"{key}=<{value_path}"]
        if archive is not None:
            command += ["--form", f"document=@{archive};type=application/zip"]
        config = f'url = "https://api.telegram.org/bot{token}/{method}"\n'
        for attempt in range(3):
            # Retry only explicit rate limits; ambiguous failures may have posted.
            response_path.unlink(missing_ok=True)
            result = subprocess.run(
                command, input=config, text=True, capture_output=True, check=False
            )
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
                if not isinstance(response, dict):
                    raise ValueError("Invalid API response")
            except (OSError, ValueError):
                raise ValueError(
                    f"Telegram returned no valid response (curl exit {result.returncode}); check connectivity and bot access"
                ) from None
            code = response.get("error_code")
            if code == 429 and attempt < 2:
                parameters = response.get("parameters")
                if not isinstance(parameters, dict):
                    raise ValueError("Telegram rate limit returned invalid retry parameters")
                wait = parameters.get("retry_after")
                if isinstance(wait, bool) or not isinstance(wait, int) or not 1 <= wait <= 120:
                    raise ValueError("Telegram rate limit requested an unsafe retry delay")
                time.sleep(wait)
                continue
            if result.returncode or response.get("ok") is not True:
                raise ValueError(
                    f"Telegram {method} failed (API code {code or 'unknown'}, curl exit {result.returncode}); check bot permissions and delivery limits"
                )
            return


def send_document(archive: Path, caption: str, token: str, chat_id: str) -> None:
    if archive.stat().st_size > DOCUMENT_LIMIT:
        raise ValueError(
            "ZIP exceeds Telegram's 50 MB hosted Bot API limit; raw payloads are in Actions artifacts. Select GitHub publishing to retain the flashable ZIP as a release asset"
        )
    telegram_request("sendDocument", {"caption": caption}, token, chat_id, archive)
    print("==> Telegram document delivered successfully")


def changelog_chunks(text: str, budget: int = 3000) -> list[str]:
    """Split before escaping: never split an entity or lose commit text."""
    chunks = []
    while text:
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if utf16_length(html.escape(text[:middle])) <= budget:
                low = middle
            else:
                high = middle - 1
        if not low:
            raise ValueError("Cannot fit a changelog character in the Telegram message budget")
        newline = text.rfind("\n", 0, low)
        end = newline + 1 if newline >= low // 2 else low
        chunks.append(text[:end])
        text = text[end:]
    return chunks


def send_changelog(text: str, title: str, token: str, chat_id: str) -> None:
    chunks = changelog_chunks(text)
    for index, chunk in enumerate(chunks, 1):
        message = f"<b>{html.escape(title)} · {index}/{len(chunks)}</b>\n<pre><code>{html.escape(chunk)}</code></pre>"
        if utf16_length(message) > 4096:
            raise ValueError("Telegram changelog message exceeds its safe length")
        if index > 1:
            time.sleep(1.1)
        telegram_request("sendMessage", {"text": message}, token, chat_id)
    print(f"==> Complete Telegram changelog delivered in {len(chunks)} message(s)")


def prepare_caption(logs: Path, meta: dict, environment: dict) -> tuple[str, str, str]:
    """Prepare a complete caption/follow-up without performing any network writes."""
    mode = environment.get("CHANGELOG_MODE", "latest-message")
    count = commit_count(environment.get("COMMIT_COUNT", "6"))
    title, notice = "📝 Latest commit", "\n… [trimmed; full message in build logs]"
    if mode == "recent-commits":
        commits = json.loads((logs / "recent-commits.json").read_text())[:count]
        if not commits:
            raise ValueError("Recent source history is empty")
        text = recent_commit_text(commits)
        title = f"📝 Recent commits ({len(commits)})"
        notice = "\n… [full changelog follows]"
    elif mode == "latest-message":
        text = (logs / "commit-message.txt").read_text()
    else:
        raise ValueError("Unknown Telegram changelog mode")
    caption = make_caption(
        meta,
        environment,
        (logs / "clang-version.txt").read_text(),
        (logs / "lld-version.txt").read_text(),
        text,
        title,
        notice,
    )
    followup = text if mode == "recent-commits" and notice.strip() in caption else ""
    heading = f"{shorten(meta['kernel_name'], 32)} · {shorten(environment['BRANCH'], 64)} · {title}"
    return caption, followup, heading


def validate_bot_token(token: str) -> None:
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise ValueError("TELEGRAM_BOT_TOKEN is not a valid bot token")


def validate_channel(channel_id: str) -> None:
    if not re.fullmatch(r"(?:-[1-9][0-9]*|@[A-Za-z][A-Za-z0-9_]{4,31})", channel_id):
        raise ValueError(
            "TELEGRAM_CHANNEL_ID must be a negative numeric channel ID or @channel username, not your personal ID"
        )


def validate_personal_chat(chat_id: str) -> None:
    if not re.fullmatch(r"[1-9][0-9]*", chat_id):
        raise ValueError(
            "TELEGRAM_CHAT_ID must be your positive numeric personal chat ID; use TELEGRAM_CHANNEL_ID for a channel"
        )


def main() -> None:
    if os.environ.get("TELEGRAM_DELIVERY_CONFIRMED") != "true":
        print("==> Personal Telegram delivery disabled; no message sent")
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise ValueError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID repository secrets")
    validate_personal_chat(chat_id)
    logs = Path("logs")
    meta = json.loads((logs / "package.json").read_text())
    caption, followup, heading = prepare_caption(logs, meta, dict(os.environ))
    send_document(Path(meta["zip_path"]), caption, token, chat_id)
    if followup:
        send_changelog(followup, heading, token, chat_id)


if __name__ == "__main__":
    run(main)

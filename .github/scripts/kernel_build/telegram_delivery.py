#!/usr/bin/env python3
"""Deliver verified build ZIPs to the user's personal Telegram chat, never a channel."""

from runtime import run
from telegram_release import deliver


def main() -> None:
    deliver(channel=False)


if __name__ == "__main__":
    run(main)

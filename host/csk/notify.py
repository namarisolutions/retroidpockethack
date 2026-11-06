"""Alert delivery for unattended monitoring.

A local log sink always runs. An optional webhook sink posts to a URL you
configure (ntfy.sh topic, Slack/Discord incoming webhook, or any JSON endpoint)
so a headless Flip can reach you. Outbound only, best-effort, short timeout.
"""
from __future__ import annotations

import json
import time
import urllib.request

LEVEL_NAME = {0: "INFO", 1: "WARN", 2: "HIGH"}


class LogSink:
    def __init__(self, path: str):
        self.path = path
        self.f = open(path, "a", buffering=1)

    def send(self, level: int, title: str, detail: str) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} [{LEVEL_NAME.get(level, '?')}] {title} - {detail}"
        self.f.write(line + "\n")
        print(line, flush=True)


class WebhookSink:
    """fmt: 'ntfy' (plain text + headers), 'slack'/'discord' (text field), 'json' (raw)."""

    def __init__(self, url: str, fmt: str = "json", min_level: int = 1, timeout: float = 6.0):
        self.url, self.fmt, self.min_level, self.timeout = url, fmt, min_level, timeout

    def send(self, level: int, title: str, detail: str) -> None:
        if level < self.min_level:
            return
        try:
            if self.fmt == "ntfy":
                data = f"{title}\n{detail}".encode()
                headers = {"Title": "CSK alert", "Priority": "urgent" if level >= 2 else "default",
                           "Tags": "warning" if level >= 2 else "eye"}
                req = urllib.request.Request(self.url, data=data, headers=headers)
            elif self.fmt in ("slack", "discord"):
                emoji = {0: ":information_source:", 1: ":warning:", 2: ":rotating_light:"}[level]
                payload = {"content" if self.fmt == "discord" else "text": f"{emoji} *{title}*\n{detail}"}
                req = urllib.request.Request(self.url, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json"})
            else:
                payload = {"level": level, "level_name": LEVEL_NAME.get(level), "title": title,
                           "detail": detail, "ts": time.time()}
                req = urllib.request.Request(self.url, data=json.dumps(payload).encode(),
                                             headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=self.timeout).read()
        except Exception as e:  # unreachable, DNS, etc. - never crash the monitor
            print(f"[notify] webhook failed: {e.__class__.__name__}", flush=True)


class Notifier:
    def __init__(self, sinks=None):
        self.sinks = sinks or []

    def send(self, level: int, title: str, detail: str) -> None:
        for s in self.sinks:
            try:
                s.send(level, title, detail)
            except Exception:
                pass

    @classmethod
    def from_config(cls, cfg: dict, log_path: str) -> "Notifier":
        sinks = [LogSink(log_path)]
        n = cfg.get("notify", {})
        if n.get("webhook"):
            sinks.append(WebhookSink(n["webhook"], n.get("webhook_format", "json"),
                                     int(n.get("min_level", 1))))
        return cls(sinks)

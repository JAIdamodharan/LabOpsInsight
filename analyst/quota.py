"""Spending protection for a public deployment that answers with a real model (your provider key is the thing being protected).

Two layers, both degrade gracefully instead of taking the site down:
  * per-visitor and global *daily* budgets for live answers. When a budget is used up the visitor still gets the scripted walkthrough.
  * cost caps for each live answer (steps, tokens, question length), so one request cannot be expensive.

Counters live in memory by default (fine for one process, weak on serverless where every instance has its own memory). Add an Upstash
Redis database (Vercel Marketplace, free tier) and the same counters become shared across all instances through its REST API.
If the shared store cannot be reached the policy fails closed: live answers stop and the scripted demo takes over.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import httpx


class MemoryStore:
    """Counters with expiry, in this process only."""
    shared = False

    def __init__(self):
        self._lock, self._items = threading.Lock(), {}

    def incr(self, key: str, ttl: int) -> int:
        now = time.time()
        with self._lock:
            n, exp = self._items.get(key, (0, 0))
            if exp <= now:
                n, exp = 0, now + ttl
            self._items[key] = (n + 1, exp)
            if len(self._items) > 5000:                                   # forget expired keys so a flood of addresses cannot grow memory
                self._items = {k: v for k, v in self._items.items() if v[1] > now}
            return n + 1


class UpstashStore:
    """The same counters in Upstash Redis over HTTPS (no driver needed)."""
    shared = True

    def __init__(self, url: str, token: str, transport=None, timeout: float = 2.0):
        self.http = httpx.Client(base_url=url.rstrip("/"), headers={"Authorization": f"Bearer {token}"}, timeout=timeout, transport=transport)

    def incr(self, key: str, ttl: int) -> int:
        r = self.http.post("/pipeline", json=[["INCR", key], ["EXPIRE", key, str(ttl), "NX"]])
        r.raise_for_status()
        return int(r.json()[0]["result"])


def store_from_env():
    url = os.environ.get("UPSTASH_REDIS_REST_URL") or os.environ.get("KV_REST_API_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN") or os.environ.get("KV_REST_API_TOKEN")
    return UpstashStore(url, token) if url and token else MemoryStore()


def _int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


@dataclass
class LivePolicy:
    """Who may get a live (paid or rate-limited) answer right now, and how expensive one answer may be."""
    store: object
    global_daily: int = 200       # live answers per day across all visitors
    ip_daily: int = 6             # per visitor address per day
    ip_minute: int = 3            # per visitor address per minute
    max_steps: int = 6            # agent steps per answer (the normal limit is 15)
    token_budget: int = 40_000    # tokens per answer (the normal limit is 150,000)
    max_question: int = 300

    @classmethod
    def from_env(cls, store=None) -> "LivePolicy":
        return cls(store or store_from_env(), _int("LABOPS_LIVE_GLOBAL_DAILY", 200), _int("LABOPS_LIVE_IP_DAILY", 6), _int("LABOPS_LIVE_IP_MINUTE", 3),
                   max(1, _int("LABOPS_LIVE_MAX_STEPS", 6)), max(2000, _int("LABOPS_LIVE_TOKEN_BUDGET", 40_000)), max(20, _int("LABOPS_LIVE_MAX_QUESTION", 300)))

    def allow(self, ip: str) -> tuple[bool, str]:
        """Count this request and say whether it may use the live model. The reason is shown to the visitor when it may not."""
        day = time.strftime("%Y%m%d", time.gmtime())
        try:
            if self.store.incr(f"labops:ip-min:{ip}:{int(time.time() // 60)}", 90) > self.ip_minute:
                return False, "You are asking quickly, so this answer uses the scripted walkthrough. Live answers resume in a minute."
            if self.store.incr(f"labops:ip-day:{ip}:{day}", 90_000) > self.ip_daily:
                return False, f"You have used today's {self.ip_daily} live answers, so this one uses the scripted walkthrough."
            if self.store.incr(f"labops:global:{day}", 90_000) > self.global_daily:
                return False, "Today's live-answer budget for this demo is used up, so this answer uses the scripted walkthrough. It resets at midnight UTC."
        except Exception:                                                 # shared store unreachable: protect the key, keep the site up
            return False, "Live answers are paused right now, so this answer uses the scripted walkthrough."
        return True, ""

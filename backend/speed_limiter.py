import time
import threading


class TokenBucket:
    """Token bucket rate limiter for download speed control."""

    def __init__(self, rate_bytes_per_sec=0):
        self.rate = rate_bytes_per_sec
        self.tokens = float(rate_bytes_per_sec) if rate_bytes_per_sec else float("inf")
        self.last_refill = time.monotonic()
        self.lock = threading.Lock()

    def set_rate(self, bytes_per_sec):
        with self.lock:
            self.rate = bytes_per_sec
            if bytes_per_sec <= 0:
                self.tokens = float("inf")
            else:
                self.tokens = min(self.tokens, float(bytes_per_sec))

    def consume(self, requested_bytes):
        if self.rate <= 0:
            return requested_bytes

        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens += elapsed * self.rate
            self.tokens = min(self.tokens, float(self.rate))
            self.last_refill = now

            allowed = min(int(self.tokens), requested_bytes)
            self.tokens -= allowed

            if allowed < requested_bytes:
                deficit = requested_bytes - allowed
                wait = deficit / self.rate
                if wait > 0:
                    time.sleep(wait)
                return requested_bytes
            return allowed


class GlobalSpeedLimiter:
    """Global speed limiter shared across all downloads."""

    def __init__(self):
        self._bucket = TokenBucket(0)
        self._enabled = False
        self._lock = threading.Lock()

    def set_global_limit(self, bytes_per_sec):
        with self._lock:
            self._enabled = bytes_per_sec > 0
            self._bucket.set_rate(bytes_per_sec)

    def is_enabled(self):
        return self._enabled

    def consume(self, requested_bytes):
        if not self._enabled:
            return requested_bytes
        return self._bucket.consume(requested_bytes)


_global_limiter = GlobalSpeedLimiter()


def get_global_limiter():
    return _global_limiter

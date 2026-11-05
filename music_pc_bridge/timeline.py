"""Pure playback timeline projection and the 12-byte BLE status packet."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import struct

STATE_UNKNOWN = 0
STATE_PLAYING = 1
STATE_PAUSED = 2
STATE_STOPPED = 3
FLAG_TIMELINE_VALID = 0x01
MAX_DURATION_MS = 24 * 60 * 60 * 1000


@dataclass(frozen=True)
class PlaybackSource:
    state: int = STATE_UNKNOWN
    start: timedelta | None = None
    end: timedelta | None = None
    position: timedelta | None = None
    last_updated: datetime | None = None
    rate: float | None = None


@dataclass(frozen=True)
class PlaybackPacket:
    state: int
    valid: bool
    elapsed_ms: int
    duration_ms: int

    def change_key(self) -> tuple[int, bool, int, int]:
        """Only send regular updates when status or displayed second changes."""
        return self.state, self.valid, self.elapsed_ms // 1000, self.duration_ms

    def pack(self, track_id: int) -> bytes:
        if not 0 <= track_id <= 255:
            raise ValueError("track_id must be 0..255")
        if not 0 <= self.state <= STATE_STOPPED:
            raise ValueError("unknown playback state")
        if not 0 <= self.elapsed_ms <= self.duration_ms <= MAX_DURATION_MS:
            raise ValueError("playback times are out of range")
        if not self.valid and (self.elapsed_ms or self.duration_ms):
            raise ValueError("invalid timelines must have zero times")
        return struct.pack(
            "<BBBBII",
            1,
            track_id,
            self.state,
            FLAG_TIMELINE_VALID if self.valid else 0,
            self.elapsed_ms,
            self.duration_ms,
        )


def state_from_winrt(status: int | None) -> int:
    """Map Windows GSMTC PlaybackStatus values to the small BLE enum."""
    if status == 4:  # PLAYING
        return STATE_PLAYING
    if status == 5:  # PAUSED
        return STATE_PAUSED
    if status in (0, 3):  # CLOSED or STOPPED
        return STATE_STOPPED
    return STATE_UNKNOWN


def project_playback(
    source: PlaybackSource | None, now: datetime | None = None
) -> PlaybackPacket:
    if source is None:
        return PlaybackPacket(STATE_UNKNOWN, False, 0, 0)

    state = source.state
    if source.start is None or source.end is None or source.position is None:
        return PlaybackPacket(state, False, 0, 0)
    try:
        duration_seconds = (source.end - source.start).total_seconds()
        position_seconds = (source.position - source.start).total_seconds()
    except (TypeError, AttributeError, OverflowError):
        return PlaybackPacket(state, False, 0, 0)
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        return PlaybackPacket(state, False, 0, 0)

    if state == STATE_PLAYING and source.last_updated is not None:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        updated = source.last_updated
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (current - updated).total_seconds())
        rate = source.rate
        if rate is None or not math.isfinite(rate) or rate <= 0:
            rate = 1.0
        position_seconds += age_seconds * rate

    duration_ms = min(MAX_DURATION_MS, int(duration_seconds * 1000))
    if duration_ms <= 0:
        return PlaybackPacket(state, False, 0, 0)
    elapsed_ms = max(0, min(duration_ms, int(position_seconds * 1000)))
    return PlaybackPacket(state, True, elapsed_ms, duration_ms)

"""Windows 11 Chrome/YouTube Music media session to ESP32-S3 BLE bridge."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path
import time

from bleak import BleakClient, BleakScanner
from winrt.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)
from winrt.windows.storage.streams import Buffer, InputStreamOptions

from cover import COVER_BYTES, COVER_SIZE, JPEG_MAX_BYTES, convert_cover, convert_cover_jpeg
from glyphs import GLYPH_MAX_BYTES, GlyphAsset, build_glyph_asset
from timeline import PlaybackSource, project_playback, state_from_winrt
from volume import get_volume_percent, set_volume_percent
from wire import (FAST_COVER_VERSION, GLYPH_VERSION, JPEG_COVER_VERSION, encode_metadata,
                  make_fast_cover_commit, make_frames)

DEVICE_NAME = "ESP32-Music-Display"
SERVICE_UUID = "d1cb460f-4eea-4594-8a7a-b6da2cde9c9c"
METADATA_CHAR_UUID = "e947ddcd-4fe0-44ca-a75d-049bb2edc7be"
COVER_CHAR_UUID = "0e60fb2f-ea19-4df2-a32a-19f81145ac17"
PLAYBACK_CHAR_UUID = "9c950662-05e7-4e55-a39e-0658ccb0c8fb"
CONTROL_CHAR_UUID = "4b717b37-4a03-4aa2-9dfc-a4bb88dfc905"
GLYPH_CHAR_UUID = "9e74967b-6875-4000-8477-f25b93b58861"
CONTROL_VERSION = 1
CONTROL_PREVIOUS = 1
CONTROL_PLAY_PAUSE = 2
CONTROL_NEXT = 3
CONTROL_VOLUME = 4
CONTROL_SEEK = 5
MIN_SEEK_INTERVAL = 0.25
SEEK_ACTION_TIMEOUT = 1.0
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024
MAX_COVER_FRAME_SIZE = 244
MIN_FAST_COVER_FRAME_SIZE = 100
COVER_CHECK_INTERVAL = 1.0
MISSING_SESSION_GRACE = 1.0
_UNCHECKED = object()


@dataclass(frozen=True)
class Metadata:
    title: str
    artist: str
    album: str


@dataclass
class MediaReading:
    metadata: Metadata
    thumbnail: object | None
    playback: PlaybackSource = PlaybackSource()


@dataclass
class PendingVolume:
    """A queued slider update whose value can be replaced before execution."""
    percent: int
    queued: bool = True


@dataclass
class PendingSeek:
    """A queued position update that can absorb newer drag notifications."""
    track_id: int
    position_ms: int
    queued: bool = True


@dataclass
class ActiveTrack:
    """The song ID and metadata most recently committed to the ESP32."""
    track_id: int | None = None
    metadata: Metadata | None = None


def track_key(metadata: Metadata) -> tuple[str, str]:
    """Chrome may fill in the album after the song and artwork arrive."""
    return metadata.title, metadata.artist


def has_usable_track(reading: MediaReading | None) -> bool:
    return reading is not None and bool(reading.metadata.title.strip())


def chrome_sessions(manager: SessionManager) -> list:
    ranked = []
    for session in manager.get_sessions():
        try:
            if not session.source_app_user_model_id.lower().startswith("chrome"):
                continue
        except Exception:
            # A media session can disappear while Windows enumerates it.
            continue
        try:
            playing = session.get_playback_info().playback_status == PlaybackStatus.PLAYING
        except Exception:
            playing = False
        ranked.append((not playing, session))
    ranked.sort(key=lambda entry: entry[0])
    return [session for _, session in ranked]


def parse_control_notification(
    data: bytes | bytearray,
) -> int | tuple[int, int] | tuple[int, int, int] | None:
    """Decode transport buttons, volume, or a track-specific seek."""
    if not data or data[0] != CONTROL_VERSION:
        return None
    if len(data) == 2 and data[1] in (CONTROL_PREVIOUS, CONTROL_PLAY_PAUSE, CONTROL_NEXT):
        return int(data[1])
    if len(data) == 3 and data[1] == CONTROL_VOLUME and data[2] <= 100:
        return CONTROL_VOLUME, int(data[2])
    if len(data) == 7 and data[1] == CONTROL_SEEK:
        return CONTROL_SEEK, int(data[2]), int.from_bytes(data[3:7], "little")
    return None


async def execute_control(manager: SessionManager, command: int | tuple[int, int]) -> bool:
    """Apply transport controls to Chrome or volume to Windows master output."""
    if isinstance(command, tuple):
        opcode, percent = command
        if opcode != CONTROL_VOLUME:
            return False
        actual = set_volume_percent(percent)
        print(f"Windows master volume: {actual}%")
        return True

    sessions = chrome_sessions(manager)
    if not sessions:
        print("Control ignored: no Chrome media session is available.")
        return False
    session = sessions[0]
    if command == CONTROL_PREVIOUS:
        name = "previous"
        action = session.try_skip_previous_async()
    elif command == CONTROL_NEXT:
        name = "next"
        action = session.try_skip_next_async()
    elif command == CONTROL_PLAY_PAUSE:
        toggle = getattr(session, "try_toggle_play_pause_async", None)
        if toggle is not None:
            # Chrome's cached status can lag a seek or another button press.
            # Let GSMTC toggle the session's current state atomically.
            try:
                toggled = bool(await toggle())
            except Exception as exc:
                print(f"Control play/pause toggle failed: {type(exc).__name__}: {exc}; "
                      "trying explicit action.")
            else:
                if toggled:
                    print("Control play/pause: accepted by Chrome.")
                    return True
                print("Control play/pause toggle rejected; trying explicit action.")
        if session.get_playback_info().playback_status == PlaybackStatus.PLAYING:
            name = "pause"
            action = session.try_pause_async()
        else:
            name = "play"
            action = session.try_play_async()
    else:
        return False
    accepted = bool(await action)
    print(f"Control {name}: {'accepted' if accepted else 'rejected'} by Chrome.")
    return accepted


def _timedelta_ticks(value: object) -> int:
    """Convert a WinRT TimeSpan exposed as Python timedelta into 100 ns ticks."""
    return (value.days * 864_000_000_000 + value.seconds * 10_000_000
            + value.microseconds * 10)


async def execute_seek(
    manager: SessionManager, seek: PendingSeek, active_track: ActiveTrack
) -> bool:
    """Seek only when the display's track still matches Chrome's current song."""
    if (active_track.track_id != seek.track_id or
            active_track.metadata is None):
        print("Seek ignored: song changed since the screen was touched.")
        return False
    sessions = chrome_sessions(manager)
    if not sessions:
        print("Seek ignored: no Chrome media session is available.")
        return False
    session = sessions[0]
    properties = await session.try_get_media_properties_async()
    if properties is None or active_track.track_id != seek.track_id:
        print("Seek ignored: Chrome song changed before playback control.")
        return False
    if ((properties.title or "") != active_track.metadata.title or
            (properties.artist or "") != active_track.metadata.artist):
        print("Seek ignored: Chrome is playing a different song.")
        return False

    timeline = session.get_timeline_properties()
    start_ticks = _timedelta_ticks(timeline.start_time)
    end_ticks = _timedelta_ticks(timeline.end_time)
    if end_ticks <= start_ticks:
        print("Seek ignored: Chrome has no valid track duration.")
        return False
    duration_ms = (end_ticks - start_ticks) // 10_000
    position_ms = min(seek.position_ms, duration_ms)
    requested_ticks = start_ticks + position_ms * 10_000
    accepted = bool(await session.try_change_playback_position_async(requested_ticks))
    print(f"Seek to {position_ms} ms: {'accepted' if accepted else 'rejected'} by Chrome.")
    return accepted


async def seek_worker(
    manager: SessionManager,
    seeks: asyncio.Queue[PendingSeek],
    active_track: ActiveTrack,
) -> None:
    """Process at most one seek at a time, independently of transport buttons."""
    last_seek_time = 0.0
    while True:
        command = await seeks.get()
        try:
            wait = MIN_SEEK_INTERVAL - (time.monotonic() - last_seek_time)
            if wait > 0:
                await asyncio.sleep(wait)
            command.queued = False
            try:
                await asyncio.wait_for(
                    execute_seek(manager, command, active_track),
                    timeout=SEEK_ACTION_TIMEOUT,
                )
            except TimeoutError:
                print("Seek timed out; continuing other controls.")
        except Exception as exc:
            print(f"Seek failed: {type(exc).__name__}: {exc}")
        finally:
            last_seek_time = time.monotonic()
            seeks.task_done()


async def _stop_seek_worker(task: asyncio.Task[None]) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def control_worker(
    manager: SessionManager,
    commands: asyncio.Queue[int | PendingVolume | PendingSeek],
    active_track: ActiveTrack | None = None,
) -> None:
    """Keep transport buttons responsive while position changes are pending."""
    seeks: asyncio.Queue[PendingSeek] = asyncio.Queue(maxsize=1)
    seek_task = asyncio.create_task(seek_worker(manager, seeks, active_track)) if active_track else None
    try:
        while True:
            command = await commands.get()
            try:
                if isinstance(command, PendingVolume):
                    command.queued = False
                    await execute_control(manager, (CONTROL_VOLUME, command.percent))
                elif isinstance(command, PendingSeek):
                    if seek_task is None:
                        command.queued = False
                        continue
                    if seeks.full():
                        superseded = seeks.get_nowait()
                        superseded.queued = False
                        seeks.task_done()
                    seeks.put_nowait(command)
                else:
                    if command in (CONTROL_PREVIOUS, CONTROL_NEXT) and seek_task is not None:
                        # A late seek on the old song could otherwise land on
                        # the new one. Drain it before sending skip.
                        await _stop_seek_worker(seek_task)
                        seeks = asyncio.Queue(maxsize=1)
                        seek_task = asyncio.create_task(seek_worker(manager, seeks, active_track))
                    accepted = await execute_control(manager, command)
                    if (accepted and command in (CONTROL_PREVIOUS, CONTROL_NEXT) and
                            active_track is not None):
                        active_track.track_id = None
                        active_track.metadata = None
            except Exception as exc:
                print(f"Control failed: {type(exc).__name__}: {exc}")
            finally:
                commands.task_done()
    finally:
        if seek_task is not None:
            await _stop_seek_worker(seek_task)


def read_playback_source(session: object) -> PlaybackSource:
    try:
        info = session.get_playback_info()
        state = state_from_winrt(int(info.playback_status))
        rate_value = info.playback_rate
        rate = float(rate_value) if rate_value is not None else None
    except Exception:
        state = 0
        rate = None
    try:
        timeline = session.get_timeline_properties()
        return PlaybackSource(
            state=state,
            start=timeline.start_time,
            end=timeline.end_time,
            position=timeline.position,
            last_updated=timeline.last_updated_time,
            rate=rate,
        )
    except Exception:
        return PlaybackSource(state=state, rate=rate)


async def read_chrome_media(manager: SessionManager) -> MediaReading | None:
    for session in chrome_sessions(manager):
        try:
            properties = await session.try_get_media_properties_async()
            if properties is None:
                continue
            return MediaReading(
                metadata=Metadata(
                    title=properties.title or "",
                    artist=properties.artist or "",
                    album=properties.album_title or "",
                ),
                thumbnail=properties.thumbnail,
                playback=read_playback_source(session),
            )
        except Exception:
            # Try another Chrome session if this one closed during the read.
            continue
    return None


async def read_thumbnail(reference: object) -> tuple[str, bytes]:
    """Read GSMTC's thumbnail stream, with a bounded allocation."""
    stream = await reference.open_read_async()
    try:
        size = int(stream.size)
        if size > MAX_THUMBNAIL_BYTES:
            raise ValueError(f"thumbnail exceeds {MAX_THUMBNAIL_BYTES} bytes")
        content_type = stream.content_type or "application/octet-stream"
        if size == 0:
            return content_type, b""
        data = bytes(
            await stream.read_async(Buffer(size), size, InputStreamOptions.NONE)
        )
        if len(data) != size:
            raise OSError(f"thumbnail read only {len(data)} of {size} bytes")
        return content_type, data
    finally:
        stream.close()


def report_thumbnail_data(
    content_type: str, data: bytes, output_path: Path | None
) -> None:
    print(f"Thumbnail: {content_type}, {len(data)} bytes")
    if output_path is not None and data:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(data)
            print(f"Thumbnail saved: {output_path}")
        except OSError as exc:
            print(f"Thumbnail could not be saved to {output_path}: {exc}")


async def thumbnail_report(
    reference: object | None, output_path: Path | None
) -> None:
    if reference is None:
        print("Thumbnail: unavailable")
        return
    try:
        content_type, data = await read_thumbnail(reference)
    except Exception as exc:
        print(f"Thumbnail: could not read ({exc})")
        return
    report_thumbnail_data(content_type, data, output_path)
    if data:
        try:
            cover = convert_cover(data)
            print(f"Cover conversion: {COVER_SIZE[0]}×{COVER_SIZE[1]} RGB565, {len(cover)} bytes")
        except Exception as exc:
            print(f"Cover conversion failed: {exc}")


async def send_metadata(client: BleakClient, value: Metadata, message_id: int) -> None:
    payload = encode_metadata(value.title, value.artist, value.album)
    for frame in make_frames(payload, message_id):
        await client.write_gatt_char(METADATA_CHAR_UUID, frame, response=True)


async def send_glyphs(
    client: BleakClient, asset: GlyphAsset, track_id: int,
    frame_size: int, *, fast: bool,
) -> None:
    """Send a CRC-checked GLY1 asset before its matching metadata update."""
    data = asset.payload
    if not 6 <= len(data) <= GLYPH_MAX_BYTES or data[:4] != b"GLY1":
        raise ValueError("invalid Unicode glyph asset")
    started = time.perf_counter()
    commit = make_fast_cover_commit(data, track_id, version=GLYPH_VERSION)
    frames = 0
    if fast:
        try:
            for frame in make_frames(
                data, track_id, max_frame_size=frame_size,
                max_message_size=GLYPH_MAX_BYTES, version=GLYPH_VERSION,
            ):
                await client.write_gatt_char(GLYPH_CHAR_UUID, frame, response=False)
                frames += 1
            await asyncio.sleep(0.04)
            await client.write_gatt_char(GLYPH_CHAR_UUID, commit, response=True)
        except Exception as exc:
            print(f"Fast glyph transfer failed ({type(exc).__name__}: {exc}); retrying reliably.")
            if not client.is_connected:
                raise
            # Old write commands can still be in WinRT's queue. Offset zero
            # starts a fresh v4 assembly on the ESP after they drain.
            await asyncio.sleep(0.10)
            frames = 0
        else:
            elapsed = time.perf_counter() - started
            print(f"Glyphs sent: {asset.glyph_count} glyphs, {len(data)} bytes, "
                  f"{frames} fast writes + CRC, {elapsed:.2f}s")
            return

    for frame in make_frames(
        data, track_id, max_frame_size=frame_size,
        max_message_size=GLYPH_MAX_BYTES, version=GLYPH_VERSION,
    ):
        await client.write_gatt_char(GLYPH_CHAR_UUID, frame, response=True)
        frames += 1
    await client.write_gatt_char(GLYPH_CHAR_UUID, commit, response=True)
    elapsed = time.perf_counter() - started
    print(f"Glyphs sent: {asset.glyph_count} glyphs, {len(data)} bytes, "
          f"{frames} reliable writes + CRC, {elapsed:.2f}s")


async def send_playback(
    client: BleakClient, source: PlaybackSource | None, track_id: int,
    volume_percent: int | None = None,
) -> tuple[int, bool, int, int, int | None]:
    packet = project_playback(source)
    payload = packet.pack(track_id)
    if volume_percent is not None:
        if not 0 <= volume_percent <= 100:
            raise ValueError("volume must be 0..100")
        payload += bytes((volume_percent,))
    await client.write_gatt_char(
        PLAYBACK_CHAR_UUID, payload, response=True
    )
    return packet.change_key() + (volume_percent,)


async def send_cover(
    client: BleakClient, data: bytes, image_id: int, frame_size: int,
    fast: bool = False,
) -> None:
    if len(data) != COVER_BYTES:
        raise ValueError(f"cover must be exactly {COVER_BYTES} bytes")
    started = time.perf_counter()
    frames = 0

    if fast:
        try:
            for frame in make_frames(
                data,
                image_id,
                max_frame_size=frame_size,
                max_message_size=COVER_BYTES,
                version=FAST_COVER_VERSION,
            ):
                await client.write_gatt_char(COVER_CHAR_UUID, frame, response=False)
                frames += 1
            # Give WinRT's transmit queue a brief chance to drain before the
            # response-bearing commit. Contiguous offsets and CRC still decide
            # whether the ESP may display the cover.
            await asyncio.sleep(0.04)
            # The write request is a barrier: the ESP commits the image only
            # after all write commands have arrived in order and CRC32 matches.
            await client.write_gatt_char(
                COVER_CHAR_UUID, make_fast_cover_commit(data, image_id),
                response=True,
            )
        except Exception as exc:
            print(f"Fast cover failed ({type(exc).__name__}: {exc}); retrying reliable transfer.")
            if not client.is_connected:
                raise
            # If WinRT rejected a command before it reached the radio, earlier
            # queued commands may still be draining. Let them finish before a
            # fresh version 1 offset-zero frame starts recovery.
            await asyncio.sleep(0.10)
            frames = 0
        else:
            elapsed = time.perf_counter() - started
            rate = len(data) / 1024 / max(elapsed, 1e-6)
            print(f"Cover sent: {len(data)} bytes, {frames} fast writes + CRC, "
                  f"{elapsed:.2f}s ({rate:.1f} KiB/s)")
            return

    for frame in make_frames(
        data,
        image_id,
        max_frame_size=frame_size,
        max_message_size=COVER_BYTES,
    ):
        await client.write_gatt_char(COVER_CHAR_UUID, frame, response=True)
        frames += 1
    elapsed = time.perf_counter() - started
    rate = len(data) / 1024 / max(elapsed, 1e-6)
    print(f"Cover sent: {len(data)} bytes, {frames} reliable writes, "
          f"{elapsed:.2f}s ({rate:.1f} KiB/s)")


async def send_jpeg_cover(
    client: BleakClient, data: bytes, image_id: int, frame_size: int,
    fast: bool,
) -> None:
    """Send a CRC-checked baseline JPEG; the ESP decodes after commit."""
    if not 4 <= len(data) <= JPEG_MAX_BYTES:
        raise ValueError("JPEG cover length is unsupported")
    started = time.perf_counter()
    frames = 0
    for frame in make_frames(
        data, image_id, max_frame_size=frame_size,
        max_message_size=JPEG_MAX_BYTES, version=JPEG_COVER_VERSION,
    ):
        await client.write_gatt_char(COVER_CHAR_UUID, frame, response=not fast)
        frames += 1
    if fast:
        # WinRT may buffer write commands. The response-bearing commit is the
        # transport barrier; CRC and contiguous offsets guard the image.
        await asyncio.sleep(0.04)
    await client.write_gatt_char(
        COVER_CHAR_UUID,
        make_fast_cover_commit(data, image_id, version=JPEG_COVER_VERSION),
        response=True,
    )
    elapsed = time.perf_counter() - started
    rate = len(data) / 1024 / max(elapsed, 1e-6)
    print(f"JPEG cover sent: {len(data)} bytes, {frames} "
          f"{'fast' if fast else 'reliable'} writes + CRC, "
          f"{elapsed:.2f}s ({rate:.1f} KiB/s)")


async def clear_cover(client: BleakClient, image_id: int) -> None:
    frame = next(make_frames(b"", image_id, allow_empty=True))
    await client.write_gatt_char(COVER_CHAR_UUID, frame, response=True)
    print("Cover cleared (thumbnail unavailable).")


async def probe(manager: SessionManager, output_path: Path | None) -> int:
    sessions = manager.get_sessions()
    if sessions:
        print("Media sessions:")
        for session in sessions:
            try:
                print(f"  {session.source_app_user_model_id}")
            except Exception:
                print("  <session closed during enumeration>")
    else:
        print("Media sessions: none")
    reading = await read_chrome_media(manager)
    if reading is None:
        print("Chrome media session not found. Start YouTube Music playback in Chrome.")
        return 1
    print(f"Title:  {reading.metadata.title}")
    print(f"Artist: {reading.metadata.artist}")
    print(f"Album:  {reading.metadata.album}")
    playback = project_playback(reading.playback)
    print(
        f"Playback: state={playback.state}, timeline_valid={playback.valid}, "
        f"elapsed_ms={playback.elapsed_ms}, duration_ms={playback.duration_ms}"
    )
    await thumbnail_report(reading.thumbnail, output_path)
    return 0


async def bridge(args: argparse.Namespace, manager: SessionManager) -> int:
    next_id = 0
    while True:
        print(f"Scanning for {args.device_name} ...")
        control_task: asyncio.Task[None] | None = None
        try:
            device = await BleakScanner.find_device_by_name(
                args.device_name, timeout=args.scan_timeout
            )
            if device is None:
                print("ESP32 not found; checking Bluetooth and scanning again.")
                if args.once:
                    return 1
                await asyncio.sleep(3)
                continue

            print(f"Connecting to {device.name} ...")
            # The firmware adds a new notify characteristic to an existing
            # service, so bypass Windows' previously cached GATT layout.
            async with BleakClient(
                device, timeout=20, winrt={"use_cached_services": False}
            ) as client:
                characteristic = client.services.get_characteristic(METADATA_CHAR_UUID)
                if characteristic is None:
                    raise RuntimeError("ESP32 metadata GATT characteristic not found")
                if "write" not in characteristic.properties:
                    raise RuntimeError("ESP32 metadata characteristic lacks write permission")
                cover_characteristic = client.services.get_characteristic(COVER_CHAR_UUID)
                if cover_characteristic is None:
                    raise RuntimeError("ESP32 cover GATT characteristic not found")
                if "write" not in cover_characteristic.properties:
                    raise RuntimeError("ESP32 cover characteristic lacks write permission")
                glyph_characteristic = client.services.get_characteristic(GLYPH_CHAR_UUID)
                if glyph_characteristic is None:
                    print("Unicode glyph characteristic unavailable; using existing text display.")
                elif "write" not in glyph_characteristic.properties:
                    print("Unicode glyph characteristic lacks write permission; using existing text display.")
                    glyph_characteristic = None
                playback_characteristic = client.services.get_characteristic(PLAYBACK_CHAR_UUID)
                if playback_characteristic is None:
                    raise RuntimeError("ESP32 playback GATT characteristic not found")
                if "write" not in playback_characteristic.properties:
                    raise RuntimeError("ESP32 playback characteristic lacks write permission")
                active_track = ActiveTrack()
                control_characteristic = client.services.get_characteristic(CONTROL_CHAR_UUID)
                if control_characteristic is None:
                    print("BLE control characteristic unavailable; track display still works.")
                elif "notify" not in control_characteristic.properties:
                    print("BLE control characteristic lacks notify support.")
                else:
                    commands: asyncio.Queue[int | PendingVolume | PendingSeek] = asyncio.Queue(maxsize=16)
                    loop = asyncio.get_running_loop()
                    pending_volume: PendingVolume | None = None
                    pending_seek: PendingSeek | None = None

                    def enqueue_control(
                        command: int | tuple[int, int] | tuple[int, int, int]
                    ) -> None:
                        nonlocal pending_volume, pending_seek
                        if isinstance(command, tuple):
                            if command[0] == CONTROL_VOLUME:
                                pending_seek = None
                                if pending_volume is not None and pending_volume.queued:
                                    pending_volume.percent = command[1]
                                    return
                                if commands.full():
                                    print("Control queue full; volume update dropped.")
                                    return
                                pending_volume = PendingVolume(command[1])
                                commands.put_nowait(pending_volume)
                                return
                            if command[0] == CONTROL_SEEK:
                                pending_volume = None
                                if (pending_seek is not None and pending_seek.queued and
                                        pending_seek.track_id == command[1]):
                                    pending_seek.position_ms = command[2]
                                    return
                                if commands.full():
                                    print("Control queue full; seek update dropped.")
                                    return
                                pending_seek = PendingSeek(command[1], command[2])
                                commands.put_nowait(pending_seek)
                                return
                            return
                        # A button separates drag updates so they retain order.
                        pending_volume = None
                        pending_seek = None
                        if commands.full():
                            print("Control queue full; button press dropped.")
                            return
                        commands.put_nowait(command)

                    def on_control(_sender: object, data: bytearray) -> None:
                        command = parse_control_notification(data)
                        if command is None:
                            print(f"Invalid BLE control notification: {bytes(data).hex()}")
                            return
                        try:
                            loop.call_soon_threadsafe(enqueue_control, command)
                        except RuntimeError:
                            # The event loop is closing while BLE disconnects.
                            pass

                    try:
                        await client.start_notify(control_characteristic, on_control)
                    except Exception as exc:
                        print(f"BLE control subscription failed: {type(exc).__name__}: {exc}")
                    else:
                        control_task = asyncio.create_task(
                            control_worker(manager, commands, active_track)
                        )
                        print("BLE media button control ready.")
                mtu = int(client.mtu_size)
                cover_frame_size = min(MAX_COVER_FRAME_SIZE, max(20, mtu - 3))
                fast_cover_available = "write-without-response" in cover_characteristic.properties
                jpeg_available = True
                print(
                    f"BLE connected. Negotiated MTU: {mtu}; "
                    f"cover write size: {cover_frame_size} bytes; "
                    f"fast cover: {'enabled' if fast_cover_available else 'unavailable'}."
                )
                last_sent: Metadata | None = None
                last_cover_key: object | bytes | None = _UNCHECKED
                desired_glyph_asset: GlyphAsset | None = None
                sent_glyph_payload: bytes | None = None
                sent_glyph_track_id: int | None = None
                last_glyph_error: str | None = None
                next_glyph_retry = 0.0
                last_playback_key: tuple[int, bool, int, int, int | None] | None = None
                next_cover_check = 0.0
                last_cover_error: str | None = None
                last_volume_error: str | None = None
                track_id = next_id
                missing_since: float | None = None

                while client.is_connected:
                    reading = await read_chrome_media(manager)
                    if not has_usable_track(reading):
                        if args.once:
                            print("Chrome media session not found; start playback first.")
                            return 1
                        if missing_since is None:
                            missing_since = time.monotonic()
                        if time.monotonic() - missing_since < MISSING_SESSION_GRACE:
                            await asyncio.sleep(args.interval)
                            continue
                        reading = None
                    else:
                        missing_since = None
                    try:
                        volume_percent = get_volume_percent()
                        last_volume_error = None
                    except Exception as exc:
                        volume_percent = None
                        error = f"{type(exc).__name__}: {exc}"
                        if error != last_volume_error:
                            print(f"Windows volume unavailable: {error}")
                            last_volume_error = error
                    value = reading.metadata if reading else Metadata("", "", "")
                    metadata_changed = value != last_sent
                    same_track = (
                        last_sent is not None and reading is not None and
                        track_key(value) == track_key(last_sent)
                    )
                    if metadata_changed:
                        next_glyph_retry = 0.0
                        if not same_track:
                            active_track.track_id = None
                            active_track.metadata = None
                            track_id = next_id
                        if reading is not None and glyph_characteristic is not None:
                            try:
                                desired_glyph_asset = build_glyph_asset(
                                    value.title, value.artist, value.album
                                )
                            except Exception as exc:
                                print(f"Unicode glyph rendering unavailable: "
                                      f"{type(exc).__name__}: {exc}")
                                desired_glyph_asset = None
                            else:
                                if (desired_glyph_asset is not None and
                                        desired_glyph_asset.omitted_count):
                                    print(f"Unicode glyph asset reached its limit; "
                                          f"{desired_glyph_asset.omitted_count} "
                                          "lower-priority glyphs omitted.")
                        else:
                            desired_glyph_asset = None
                    if (glyph_characteristic is not None and
                            desired_glyph_asset is not None and
                            time.monotonic() >= next_glyph_retry and
                            (sent_glyph_track_id != track_id or
                             sent_glyph_payload != desired_glyph_asset.payload)):
                        current_mtu = int(client.mtu_size)
                        glyph_frame_size = min(
                            MAX_COVER_FRAME_SIZE, max(20, current_mtu - 3)
                        )
                        fast_glyph_size = min(
                            glyph_frame_size,
                            int(getattr(glyph_characteristic,
                                        "max_write_without_response_size",
                                        glyph_frame_size)),
                        )
                        fast_glyph = (
                            "write-without-response" in glyph_characteristic.properties
                            and fast_glyph_size >= MIN_FAST_COVER_FRAME_SIZE
                        )
                        try:
                            await send_glyphs(
                                client, desired_glyph_asset, track_id,
                                fast_glyph_size if fast_glyph else glyph_frame_size,
                                fast=fast_glyph,
                            )
                        except Exception as exc:
                            if not client.is_connected:
                                raise
                            error = f"{type(exc).__name__}: {exc}"
                            if error != last_glyph_error:
                                print(f"Unicode glyph transfer unavailable: {error}")
                                last_glyph_error = error
                            next_glyph_retry = time.monotonic() + 2.0
                        else:
                            sent_glyph_payload = desired_glyph_asset.payload
                            sent_glyph_track_id = track_id
                            last_glyph_error = None
                    if metadata_changed:
                        await send_metadata(client, value, track_id)
                        active_track.track_id = track_id
                        active_track.metadata = value if reading else None
                        last_sent = value
                        if not same_track:
                            last_cover_key = _UNCHECKED
                            next_cover_check = 0.0
                            last_playback_key = await send_playback(
                                client, reading.playback if reading else None, track_id,
                                volume_percent,
                            )
                        if reading is None:
                            print("Sent empty metadata (no Chrome media session).")
                        elif same_track:
                            print(f"Updated album on track {track_id}: {value.album!r}")
                        else:
                            print(
                                f"Sent: {value.title!r} / {value.artist!r} / "
                                f"{value.album!r}"
                            )

                    cover_action_sent = False
                    if time.monotonic() >= next_cover_check:
                        next_cover_check = time.monotonic() + COVER_CHECK_INTERVAL
                        if reading is None or reading.thumbnail is None:
                            if last_cover_key is _UNCHECKED or last_cover_key is not None:
                                await clear_cover(client, track_id)
                                last_cover_key = None
                                cover_action_sent = True
                            last_cover_error = None
                        else:
                            try:
                                content_type, raw = await read_thumbnail(reading.thumbnail)
                                if not raw:
                                    raise ValueError("thumbnail stream is empty")
                                digest = hashlib.sha256(raw).digest()
                                changed = last_cover_key is _UNCHECKED or digest != last_cover_key
                                if changed:
                                    cover = None
                                    jpeg_cover = None
                                    if jpeg_available:
                                        try:
                                            jpeg_cover = convert_cover_jpeg(raw)
                                        except Exception as exc:
                                            print(f"JPEG encoding skipped: {exc}")
                                    if jpeg_cover is None:
                                        cover = convert_cover(raw)
                            except Exception as exc:
                                error = f"{type(exc).__name__}: {exc}"
                                if error != last_cover_error:
                                    print(f"Cover unavailable: {error}")
                                    last_cover_error = error
                            else:
                                if changed:
                                    current_mtu = int(client.mtu_size)
                                    if current_mtu != mtu:
                                        mtu = current_mtu
                                        cover_frame_size = min(
                                            MAX_COVER_FRAME_SIZE, max(20, mtu - 3)
                                        )
                                        print(
                                            f"MTU updated: {mtu}; cover write size: "
                                            f"{cover_frame_size} bytes."
                                        )
                                    report_thumbnail_data(content_type, raw, args.thumbnail_out)
                                    fast_frame_size = min(
                                        cover_frame_size,
                                        int(getattr(cover_characteristic,
                                                    "max_write_without_response_size",
                                                    cover_frame_size)),
                                    )
                                    use_fast_cover = (
                                        fast_cover_available and
                                        fast_frame_size >= MIN_FAST_COVER_FRAME_SIZE
                                    )
                                    jpeg_sent = False
                                    if jpeg_cover is not None:
                                        try:
                                            await send_jpeg_cover(
                                                client, jpeg_cover, track_id,
                                                fast_frame_size if use_fast_cover
                                                else cover_frame_size,
                                                fast=use_fast_cover,
                                            )
                                        except Exception as exc:
                                            if not client.is_connected:
                                                raise
                                            jpeg_available = False
                                            print(
                                                f"JPEG cover unavailable "
                                                f"({type(exc).__name__}: {exc}); "
                                                "using RGB565 compatibility mode."
                                            )
                                            await asyncio.sleep(0.10)
                                        else:
                                            jpeg_sent = True
                                    if not jpeg_sent:
                                        if cover is None:
                                            cover = convert_cover(raw)
                                        await send_cover(
                                            client, cover, track_id,
                                            fast_frame_size if use_fast_cover
                                            else cover_frame_size,
                                            fast=use_fast_cover,
                                        )
                                    last_cover_key = digest
                                    cover_action_sent = True
                                last_cover_error = None
                    playback_packet = project_playback(
                        reading.playback if reading else None
                    )
                    playback_key = playback_packet.change_key() + (volume_percent,)
                    if cover_action_sent or playback_key != last_playback_key:
                        last_playback_key = await send_playback(
                            client, reading.playback if reading else None,
                            track_id, volume_percent,
                        )
                    if metadata_changed and not same_track:
                        next_id = (next_id + 1) & 0xFF
                    if args.once:
                        return 0
                    await asyncio.sleep(args.interval)

                print("BLE disconnected.")
        except Exception as exc:
            print(f"Bridge error: {type(exc).__name__}: {exc}")
            if args.once:
                return 1
        finally:
            if control_task is not None:
                control_task.cancel()
                try:
                    await control_task
                except asyncio.CancelledError:
                    pass
        await asyncio.sleep(3)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send Chrome/YouTube Music metadata to ESP32 over BLE"
    )
    parser.add_argument(
        "--probe", action="store_true", help="read GSMTC metadata without BLE"
    )
    parser.add_argument(
        "--once", action="store_true", help="send the current track once and exit"
    )
    parser.add_argument("--device-name", default=DEVICE_NAME)
    parser.add_argument("--scan-timeout", type=float, default=10.0)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument(
        "--thumbnail-out",
        type=Path,
        help="save the current thumbnail bytes locally (not sent to ESP32)",
    )
    args = parser.parse_args()
    if args.scan_timeout <= 0 or args.interval <= 0:
        parser.error("--scan-timeout and --interval must be positive")
    return args


async def main() -> int:
    args = parse_args()
    manager = await SessionManager.request_async()
    if args.probe:
        return await probe(manager, args.thumbnail_out)
    return await bridge(args, manager)


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("Stopped.")

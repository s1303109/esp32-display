"""Render a 320x240 review image matching the pending LVGL music layout."""

from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
SOURCE = ROOT / "gzui2qvgo5o51.jpg"
SCALE = 4


def scaled_box(box):
    return tuple(round(value * SCALE) for value in box)


def dominant_color(image):
    sample = image.resize((130, 130), Image.Resampling.LANCZOS)
    samples = [sample.getpixel((x, y))[:3] for y in range(0, 130, 2) for x in range(0, 130, 2)]
    def bucket(color):
        return tuple(channel >> 5 for channel in color)
    def midtone(color):
        brightness = (54 * color[0] + 183 * color[1] + 19 * color[2]) >> 8
        return 28 < brightness < 230 and max(color) - min(color) >= 22
    valid = [color for color in samples if midtone(color)]
    if len(valid) < 20:
        valid = samples
    winner = Counter(map(bucket, valid)).most_common(1)[0][0]
    chosen = [color for color in valid if bucket(color) == winner]
    return tuple(sum(color[i] for color in chosen) // len(chosen) for i in range(3))


def mix(a, b, pct):
    return tuple(round((100 - pct) * a[i] / 100 + pct * b[i] / 100) for i in range(3))


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/{name}", size * SCALE)


def point(x, y):
    return (x * SCALE, y * SCALE)


def render(show_volume=False, dragging=False, seek_dragging=False, paused=False):
    source = Image.open(SOURCE).convert("RGB")
    art = source.crop((120, 116, 636, 632)).resize((130 * SCALE, 130 * SCALE), Image.Resampling.LANCZOS)
    cover_small = art.resize((130, 130), Image.Resampling.LANCZOS)
    dominant = dominant_color(cover_small)
    base = tuple(min(100, v) for v in mix((9, 12, 20), dominant, 44))
    track = mix(base, (255, 255, 255), 22)
    panel = mix(base, (0, 0, 0), 20)
    canvas = Image.new("RGB", (320 * SCALE, 240 * SCALE), base)
    draw = ImageDraw.Draw(canvas)

    # Album image, matching the 130x130 LVGL image with rounded container.
    mask = Image.new("L", art.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, art.width - 1, art.height - 1), radius=12 * SCALE, fill=255)
    canvas.paste(art, point(14, 54), mask)

    draw.text(point(158, 39), "YOUTUBE MUSIC", font=font(9, True), fill=(192, 201, 208))
    draw.text(point(158, 64), "Ask the Birds", font=font(18, True), fill="white")
    draw.text(point(158, 85), "in Cairo", font=font(18, True), fill="white")
    draw.text(point(158, 112), "Felukah", font=font(13), fill=(210, 212, 219))

    # Previous, pause, next icons. Hitboxes are wider in the real LVGL UI.
    white = (255, 255, 255)
    draw.polygon([point(174, 156), point(187, 149), point(187, 163)], fill=white)
    draw.polygon([point(182, 156), point(195, 149), point(195, 163)], fill=white)
    if paused:
        draw.polygon([point(221, 147), point(221, 169), point(243, 158)], fill=white)
    else:
        draw.rounded_rectangle(scaled_box((223, 147, 228, 169)), radius=1 * SCALE, fill=white)
        draw.rounded_rectangle(scaled_box((234, 147, 239, 169)), radius=1 * SCALE, fill=white)
    draw.polygon([point(277, 149), point(277, 163), point(290, 156)], fill=white)
    draw.polygon([point(285, 149), point(285, 163), point(298, 156)], fill=white)

    # Position and negative remaining time match the requested reference.
    if seek_dragging:
        draw.rounded_rectangle(scaled_box((147, 190, 317, 196)), radius=3 * SCALE,
                               fill=mix(track, white, 16))
        draw.rounded_rectangle(scaled_box((147, 190, 285, 196)), radius=3 * SCALE, fill=white)
        elapsed = "1:35"
        remaining = "-0:22"
    else:
        draw.rounded_rectangle(scaled_box((157, 191, 307, 195)), radius=2 * SCALE, fill=track)
        draw.rounded_rectangle(scaled_box((157, 191, 257, 195)), radius=2 * SCALE, fill=white)
        elapsed = "1:18"
        remaining = "-0:39"
    draw.text(point(157, 202), elapsed, font=font(11), fill=(208, 214, 219))
    left = 307 * SCALE - draw.textbbox((0, 0), remaining, font=font(11))[2]
    draw.text((left, 202 * SCALE), remaining, font=font(11), fill=(208, 214, 219))

    # Small speaker aligned with the slider, just above the right-hand title.
    draw.polygon([point(238, 16), point(242, 16), point(249, 11), point(249, 27),
                  point(242, 22), point(238, 22)], fill=white)
    draw.arc(scaled_box((243, 10, 260, 28)), -64, 64, fill=white, width=SCALE)
    draw.arc(scaled_box((246, 13, 257, 25)), -64, 64, fill=white, width=SCALE)

    if show_volume:
        # Small, frame-free slider. The whole line grows slightly while
        # pressed, matching the LVGL slider's pressed state; there is no knob.
        if dragging:
            draw.rounded_rectangle(scaled_box((92, 17, 228, 22)), radius=3 * SCALE,
                                   fill=mix(track, white, 16))
            draw.rounded_rectangle(scaled_box((92, 17, 176, 22)), radius=3 * SCALE, fill=white)
        else:
            draw.rounded_rectangle(scaled_box((104, 17, 216, 21)), radius=2 * SCALE,
                                   fill=track)
            draw.rounded_rectangle(scaled_box((104, 17, 173, 21)), radius=2 * SCALE,
                                   fill=white)

    if seek_dragging:
        label = "music_display_phone_style_seek_dragging"
    elif paused:
        label = "music_display_phone_style_paused"
    elif dragging:
        label = "music_display_phone_style_volume_dragging"
    elif show_volume:
        label = "music_display_phone_style_volume"
    else:
        label = "music_display_phone_style"
    exact = canvas.resize((320, 240), Image.Resampling.LANCZOS)
    exact.save(OUT / f"{label}_320x240.png")
    exact.resize((1280, 960), Image.Resampling.NEAREST).save(OUT / f"{label}_4x.png")
    return label, dominant, base


if __name__ == "__main__":
    print(render(False))
    print(render(True))
    print(render(True, dragging=True))
    print(render(False, seek_dragging=True))
    print(render(False, paused=True))

# Bundled Noto Sans CJK font

`NotoSansCJK-Regular.ttc` is the unmodified regular-weight OpenType
collection from the [official Noto CJK repository](https://github.com/notofonts/noto-cjk/blob/main/Sans/OTC/NotoSansCJK-Regular.ttc).
The accompanying [OFL.txt](OFL.txt) is the repository's
[Sans/LICENSE](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE):
SIL Open Font License 1.1. Copyright © 2014–2021 Adobe. Keep the license
with redistributed copies of the font.

- File size: 19,484,784 bytes.
- SHA-256: `b76b0433203017ca80401b2ee0dd69350349871c4b19d504c34dbdd80541690a`.
- Pillow face indices for proportional text: Japanese `0`, Korean `1`,
  Simplified Chinese `2`, Traditional Chinese `3`, Hong Kong `4`.
- Indices `5` through `9` are the corresponding monospaced faces.

Each proportional face has 44,810 Unicode code points in its cmap. The
collection includes 20,976 CJK unified ideographs in U+4E00–U+9FFF, all
11,172 precomposed Hangul syllables in U+AC00–U+D7AF, 93 hiragana, 96
katakana, ASCII, and `í` (U+00ED). The font does not contain color emoji.
These counts were checked against the downloaded TTC with fontTools; the
Traditional Chinese, Japanese, and Korean faces were also rendered with
Pillow's `ImageFont.truetype(path, size, index=...)`.

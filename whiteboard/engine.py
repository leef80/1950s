"""白板手绘动画引擎：白底、黑色马克笔一笔一笔画出笔画和文字。

场景由 Timeline 描述：在某个时间段内画完一组笔画，或写出一行字。
render_film() 把多个场景依次渲染成一个 mp4，场景之间淡出到白板。
"""
import math
import os
import random
import subprocess
import urllib.request

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
SS = 2                      # 超采样倍数，用于抗锯齿
FPS = 30
INK = (22, 22, 24)
PAPER = (250, 250, 247)
FADE = 0.6                  # 场景结尾淡出到白板的时长（秒）
PEN_LEAVES = 1.0            # 场景结尾提前多久把笔移出画面

HERE = os.path.dirname(os.path.abspath(__file__))
# 手写风格字体：霞鹜文楷（SIL OFL）。首次运行自动下载，失败时退回文泉驿正黑。
HAND_FONT = os.path.join(HERE, "fonts", "LXGWWenKai-Regular.ttf")
HAND_FONT_URL = ("https://github.com/lxgw/LxgwWenKai/releases/download/"
                 "v1.330/LXGWWenKai-Regular.ttf")
FALLBACK_FONT = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"

rng = random.Random(1956)


def font_path():
    if not os.path.exists(HAND_FONT):
        try:
            os.makedirs(os.path.dirname(HAND_FONT), exist_ok=True)
            urllib.request.urlretrieve(HAND_FONT_URL, HAND_FONT)
        except OSError as e:
            print(f"下载手写字体失败（{e}），改用文泉驿正黑")
            return FALLBACK_FONT
    return HAND_FONT


# ---------------------------------------------------------------- 几何工具

def resample(points, step=5.0):
    out = [points[0]]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        d = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(d / step))
        for i in range(1, n + 1):
            out.append((x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n))
    return out


def wobble(points, amp=1.3):
    """给直线加一点手抖：沿法线方向的低频正弦偏移。"""
    pts = resample(points)
    if len(pts) < 3:
        return pts
    f1, f2 = rng.uniform(0.02, 0.05), rng.uniform(0.08, 0.14)
    p1, p2 = rng.uniform(0, 6.28), rng.uniform(0, 6.28)
    out, s = [], 0.0
    for i, (x, y) in enumerate(pts):
        a = pts[max(i - 1, 0)]
        b = pts[min(i + 1, len(pts) - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1
        nx, ny = -dy / L, dx / L
        if i:
            s += math.hypot(x - pts[i - 1][0], y - pts[i - 1][1])
        o = amp * (0.7 * math.sin(s * f1 + p1) + 0.3 * math.sin(s * f2 + p2))
        out.append((x + nx * o, y + ny * o))
    return out


def ellipse(cx, cy, rx, ry, a0=0, a1=360, n=None):
    n = n or max(12, int((abs(a1 - a0) / 360) * (rx + ry) * 1.2))
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n)))
            for i in range(n + 1)]


def circle(cx, cy, r):
    return ellipse(cx, cy, r, r, -90, 272)


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0 - 1.5)]


def rounded_rect(x0, y0, x1, y1, r=12):
    pts = []
    for cx, cy, a in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                      (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        pts += ellipse(cx, cy, r, r, a, a + 90, 6)
    return pts + [pts[0]]


def speech_bubble(x0, y0, x1, y1, tail):
    """圆角对话框 + 指向 tail 的小尾巴。返回笔画列表。"""
    tx, ty = tail
    mx = min(max(tx, x0 + 22), x1 - 22)
    return [rounded_rect(x0, y0, x1, y1),
            [(mx - 9, y1), (tx, ty), (mx + 9, y1)]]


def cloud(cx, cy, rx, ry, bumps=10):
    """思考气泡：椭圆上相邻两点之间各画一段向外鼓的半圆。"""
    ring = [(cx + rx * math.cos(2 * math.pi * i / bumps),
             cy + ry * math.sin(2 * math.pi * i / bumps)) for i in range(bumps + 1)]
    pts = []
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        r = math.hypot(x1 - x0, y1 - y0) / 2
        a = math.degrees(math.atan2(y0 - my, x0 - mx))
        # 选向外鼓的方向：半圆中点要比弦中点离椭圆中心更远
        out = math.radians(a + 90)
        sign = 1 if math.hypot(mx + math.cos(out) * r - cx, my + math.sin(out) * r - cy) \
            > math.hypot(mx - cx, my - cy) else -1
        pts += ellipse(mx, my, r, r, a, a + sign * 180, 10)
    return pts


def arrow(x0, y0, x1, y1, head=14):
    a = math.atan2(y1 - y0, x1 - x0)
    return [[(x0, y0), (x1, y1)],
            [(x1 - head * math.cos(a - 0.45), y1 - head * math.sin(a - 0.45)), (x1, y1),
             (x1 - head * math.cos(a + 0.45), y1 - head * math.sin(a + 0.45))]]


def check(x, y, s=1.0):
    return [[(x, y + 10 * s), (x + 9 * s, y + 22 * s), (x + 30 * s, y - 8 * s)]]


def length(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


# ---------------------------------------------------------------- 时间线

class Timeline:
    """一个场景。items 中每项为 (t0, t1, kind, payload)。"""

    HOLD = 2.4              # 画完之后停留的时间（含淡出）

    def __init__(self, duration=None):
        self._duration = duration
        self.items = []

    @property
    def duration(self):
        if self._duration is not None:
            return self._duration
        return max(t1 for _, t1, _, _ in self.items) + self.HOLD

    def strokes(self, t0, t1, strokes, width=4.5, gap=0.1):
        """在 [t0,t1] 内按长度比例依次画完一组笔画（笔画之间有抬笔间隔）。"""
        strokes = [wobble(s) for s in strokes]
        lens = [length(s) for s in strokes]
        gap = min(gap, (t1 - t0) * 0.3 / max(1, len(strokes)))
        draw_time = (t1 - t0) - gap * (len(strokes) - 1)
        t = t0
        for s, L in zip(strokes, lens):
            d = draw_time * L / sum(lens)
            self.items.append((t, t + d, "stroke", (s, width)))
            t += d + gap
        return t1

    def text(self, t0, t1, x, y, txt, size):
        self.items.append((t0, t1, "text", (x, y, txt, size)))
        return t1

    def text_center(self, t0, t1, cx, y, txt, size):
        return self.text(t0, t1, int(cx - text_width(txt, size) / 2), y, txt, size)


# ---------------------------------------------------------------- 渲染

_font_cache = {}
_mask_cache = {}


def _font(size):
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(font_path(), size * SS)
    return _font_cache[size]


def text_width(txt, size):
    return _font(size).getlength(txt) / SS


def text_mask(txt, size):
    key = (txt, size)
    if key not in _mask_cache:
        font = _font(size)
        x0, y0, x1, y1 = font.getbbox(txt)
        mask = Image.new("L", (int(x1) + 4, int(y1) + 8), 0)
        # stroke_width 让细字体也有马克笔的粗度
        ImageDraw.Draw(mask).text((0, 0), txt, font=font, fill=255,
                                  stroke_width=max(1, size * SS // 40), stroke_fill=255)
        _mask_cache[key] = mask
    return _mask_cache[key]


def draw_marker(img, x, y):
    """画一支斜握的白板笔，笔尖落在 (x, y)。"""
    d = ImageDraw.Draw(img, "RGBA")
    ux, uy = 0.55, 0.835                       # 笔身方向（向右下）
    px, py = -uy, ux                           # 法线

    def at(a, b):
        return (x + ux * a + px * b, y + uy * a + py * b)
    d.polygon([at(0, 0), at(14, -4), at(14, 4)], fill=(30, 30, 30, 255))          # 笔尖
    d.polygon([at(14, -7), at(30, -11), at(30, 11), at(14, 7)], fill=(45, 45, 48, 255))
    d.polygon([at(30, -13), at(170, -13), at(170, 13), at(30, 13)], fill=(232, 232, 228, 255))
    d.line([at(30, -13), at(170, -13), at(170, 13), at(30, 13), at(30, -13)],
           fill=(120, 120, 120, 255), width=2)
    d.polygon([at(70, -13), at(110, -13), at(110, 13), at(70, 13)], fill=(25, 25, 25, 255))
    d.polygon([at(170, -11), at(200, -11), at(200, 11), at(170, 11)], fill=(35, 35, 38, 255))


def scene_frames(tl):
    """逐帧生成一个场景的画面（PIL Image，W×H）。"""
    canvas = Image.new("RGB", (W * SS, H * SS), PAPER)
    cd = ImageDraw.Draw(canvas)
    progress = [0.0] * len(tl.items)
    last_pen = None
    paper = Image.new("RGB", (W, H), PAPER)
    for f in range(int(round(tl.duration * FPS))):
        t = f / FPS
        pen = None
        for idx, (t0, t1, kind, data) in enumerate(tl.items):
            if t < t0 or progress[idx] >= 1.0:
                continue
            p = min(1.0, (t - t0) / max(1e-6, t1 - t0))
            if kind == "stroke":
                pts, width = data
                n = len(pts)
                a = int(progress[idx] * (n - 1))
                b = max(a + 1, int(p * (n - 1)))
                seg = [(px * SS, py * SS) for px, py in pts[a:b + 1]]
                w = int(width * SS)
                cd.line(seg, fill=INK, width=w, joint="curve")
                r = w / 2
                for sx, sy in (seg[0], seg[-1]):
                    cd.ellipse([sx - r, sy - r, sx + r, sy + r], fill=INK)
                pen = pts[min(b, n - 1)]
            else:
                x, y, txt, size = data
                mask = text_mask(txt, size)
                mw, mh = mask.size
                cut = int(mw * p)
                if cut > 0:
                    canvas.paste(INK, (x * SS, y * SS, x * SS + cut, y * SS + mh),
                                 mask.crop((0, 0, cut, mh)))
                # 笔尖在字里上下快速移动，模拟书写
                wig = 0.35 + 0.45 * (0.5 + 0.5 * math.sin(t * 38))
                pen = (x + cut / SS, y + mh / SS * wig)
            progress[idx] = p
        if pen:
            last_pen = pen
        frame = canvas.reduce(SS)
        if last_pen and t < tl.duration - PEN_LEAVES:
            draw_marker(frame, *last_pen)
        fade = (t - (tl.duration - FADE)) / FADE
        if fade > 0:
            frame = Image.blend(frame, paper, min(1.0, fade))
        yield frame


def render_film(scenes, out_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen([
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", out_path,
    ], stdin=subprocess.PIPE)
    for i, tl in enumerate(scenes):
        print(f"渲染场景 {i + 1}/{len(scenes)}（{tl.duration:.0f} 秒）", flush=True)
        for frame in scene_frames(tl):
            proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()

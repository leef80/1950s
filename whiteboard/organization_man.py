"""白板手绘动画：1956 年，一个"组织人"的早晨 8:59（约 30 秒）。

白底、黑色马克笔一笔一笔画出：巨大的公司大楼、一排一模一样的上班族，
最后从"组织"的提线木偶横杆上垂下提线，牵住每一个人。

用法: python3 organization_man.py [输出路径.mp4]
依赖: pillow, numpy, imageio-ffmpeg；中文字体 WenQuanYi Zen Hei。
"""
import math
import random
import subprocess
import sys

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 720
SS = 2                      # 超采样倍数，用于抗锯齿
FPS = 30
DURATION = 30.0
FONT_PATH = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
INK = (22, 22, 24)
PAPER = (250, 250, 247)

rng = random.Random(1956)


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


def rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0 - 1.5)]


def length(pts):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


# ---------------------------------------------------------------- 时间线
# 每个动作: ("stroke", 点列, 线宽) 或 ("text", x, y, 文本, 字号)

class Timeline:
    def __init__(self):
        self.items = []     # (t0, t1, kind, payload)

    def strokes(self, t0, t1, strokes, width=4.5, gap=0.12):
        """在 [t0,t1] 内按长度比例依次画完一组笔画（笔画之间有抬笔间隔）。"""
        strokes = [wobble(s) for s in strokes]
        lens = [length(s) for s in strokes]
        total_gap = gap * (len(strokes) - 1)
        draw_time = max(0.05, (t1 - t0) - total_gap)
        t = t0
        for s, L in zip(strokes, lens):
            d = draw_time * L / sum(lens)
            self.items.append((t, t + d, "stroke", (s, width)))
            t += d + gap

    def text(self, t0, t1, x, y, txt, size):
        self.items.append((t0, t1, "text", (x, y, txt, size)))


def man(x, y=640, s=1.0):
    """一个面目模糊、戴礼帽、提公文包、向右走的上班族。返回笔画列表。"""
    def P(*pts):
        return [(x + px * s, y + py * s) for px, py in pts]
    return [
        # 帽子：帽顶 + 帽檐 + 帽带
        P((-13, -146), (-12, -162), (-5, -167), (0, -163), (5, -167), (12, -162), (13, -146)),
        P((-25, -145), (-10, -147), (10, -147), (27, -144)),
        P((-12, -151), (12, -151)),
        # 头（被帽檐挡住的下半圈；没有五官）
        [(x + px, y + py) for px, py in ellipse(0, -132 * s, 13 * s, 14 * s, -20, 200)],
        # 脖子 + 西装躯干
        P((-5, -118), (-5, -114)), P((5, -118), (5, -114)),
        P((-6, -114), (-21, -110), (-18, -64), (18, -64), (21, -110), (6, -114)),
        # 翻领 + 领带
        P((-6, -114), (0, -96), (6, -114)),
        P((0, -112), (-3, -98), (0, -84), (3, -98), (0, -112)),
        # 腿（走路姿势）+ 鞋
        P((-9, -64), (-20, -2)), P((-20, -2), (-8, 0)),
        P((9, -64), (17, -2)), P((17, -2), (30, 0)),
        # 后手臂、前手臂
        P((-20, -108), (-30, -84), (-34, -66)),
        P((20, -108), (27, -84), (30, -66)),
        # 公文包
        P((18, -66), (44, -66), (44, -40), (18, -40), (18, -66)),
        P((25, -66), (27, -72), (35, -72), (37, -66)),
    ]


def build_timeline():
    tl = Timeline()

    # 1. 标题
    tl.text(0.3, 2.4, 60, 42, "1956年，周一，上午 8:59", 40)

    # 2. 公司大楼
    tl.strokes(2.5, 4.2, [
        [(820, 640), (820, 150), (1160, 150), (1160, 640)],
        [(870, 150), (870, 95), (1110, 95), (1110, 150)],
        [(990, 95), (990, 48)], ellipse(990, 42, 6, 6),
    ], width=5)
    tl.strokes(4.2, 4.9, [rect(840, 160, 1140, 198)], width=4)
    tl.text(4.9, 6.1, 866, 165, "CONSOLIDATED CORP.", 26)
    # 大钟：9:00
    tl.strokes(6.1, 7.2, [
        ellipse(990, 262, 44, 44, -90, 272),
        [(990, 262), (990, 228)],          # 分针指 12
        [(990, 262), (966, 262)],          # 时针指 9
    ] + [[(990 + 37 * math.cos(math.radians(a)), 262 + 37 * math.sin(math.radians(a))),
          (990 + 42 * math.cos(math.radians(a)), 262 + 42 * math.sin(math.radians(a)))]
         for a in range(0, 360, 90)], width=4, gap=0.06)
    # 窗户网格：一格一格，整齐划一
    wins = []
    for row in range(5):
        for col in range(5):
            x0, y0 = 842 + col * 62, 330 + row * 42
            wins.append(rect(x0, y0, x0 + 46, y0 + 28))
    tl.strokes(7.2, 9.6, wins, width=3, gap=0.02)
    # 大门
    tl.strokes(9.6, 10.3, [
        [(945, 640), (945, 565), (1035, 565), (1035, 640)],
        [(990, 565), (990, 640)],
    ], width=4.5)

    # 3. 地面
    tl.strokes(10.3, 10.8, [[(40, 640), (1240, 640)]], width=4)

    # 4. 一排一模一样的人——第一个画得慢，后面越画越快（像流水线）
    xs = [700, 590, 480, 370, 260, 150]
    tl.strokes(10.9, 14.2, man(xs[0]), width=4, gap=0.06)
    t = 14.3
    for i, x in enumerate(xs[1:]):
        d = [1.25, 1.05, 0.9, 0.8, 0.7][i]
        tl.strokes(t, t + d, man(x), width=4, gap=0.015)
        t += d + 0.08                                     # t ≈ 19.4

    # 5. 底部字幕
    tl.text(19.5, 22.6, 60, 662, "同样的帽子，同样的公文包，同样的 9:00。", 32)

    # 6. 提线木偶：组织的横杆 + 每个人头顶的提线
    tl.strokes(22.8, 23.7, [
        rect(110, 258, 740, 296),
        [(425, 258), (425, 222)], [(385, 222), (465, 222)],
    ], width=5)
    tl.text(23.7, 24.5, 397, 263, "组织", 26)
    tl.strokes(24.5, 26.0, [[(x, 296), (x, 470)] for x in xs], width=2.5, gap=0.04)
    # 横杆挂在大楼上
    tl.strokes(26.0, 26.4, [[(740, 279), (790, 230), (840, 199)]], width=3.5)

    # 7. 结论
    tl.text(26.5, 29.0, 60, 98, "他不只是为组织工作——他属于组织。", 40)
    return tl


# ---------------------------------------------------------------- 渲染

_font_cache = {}


def text_layer(txt, size):
    key = (txt, size)
    if key not in _font_cache:
        font = ImageFont.truetype(FONT_PATH, size * SS)
        x0, y0, x1, y1 = font.getbbox(txt)
        mask = Image.new("L", (x1 + 4, y1 + 8), 0)
        ImageDraw.Draw(mask).text((0, 0), txt, font=font, fill=255)
        _font_cache[key] = mask
    return _font_cache[key]


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


def render(out_path):
    tl = build_timeline()
    canvas = Image.new("RGB", (W * SS, H * SS), PAPER)
    cd = ImageDraw.Draw(canvas)
    progress = [0.0] * len(tl.items)      # 每个动作已画到的比例
    last_pen = None

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen([
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", out_path,
    ], stdin=subprocess.PIPE)

    n_frames = int(DURATION * FPS)
    for f in range(n_frames):
        t = f / FPS
        pen = None
        for idx, (t0, t1, kind, data) in enumerate(tl.items):
            if t < t0 or progress[idx] >= 1.0:
                continue
            p = min(1.0, (t - t0) / (t1 - t0))
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
                mask = text_layer(txt, size)
                mw, mh = mask.size
                cut = int(mw * p)
                if cut > 0:
                    canvas.paste(INK, (x * SS, y * SS, x * SS + cut, y * SS + mh),
                                 mask.crop((0, 0, cut, mh)))
                # 笔尖在字里上下快速移动，模拟书写
                wig = 0.5 + 0.4 * math.sin(t * 38)
                pen = (x + cut / SS, y + mh / SS * wig)
            progress[idx] = p
        if pen:
            last_pen = pen
        frame = canvas.resize((W, H), Image.LANCZOS)
        # 画完之后笔离开画面，最后 1 秒只留成品
        if last_pen and t < 29.0:
            draw_marker(frame, *last_pen)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()


if __name__ == "__main__":
    render(sys.argv[1] if len(sys.argv) > 1 else "organization_man_30s.mp4")

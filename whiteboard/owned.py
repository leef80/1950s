"""《我归组织所有》：75 秒白板手绘短视频。

1950 年代的一个组织人，第一人称讲他走不掉、挣不脱、认了命，又忍不住幻想。
灵感来自威廉·H·怀特《组织人》（The Organization Man, 1956）。
文案、节奏和画面里的字都在 owned.toml 里；每样东西在旁白念到它的那一小句时画出来。

用法: python3 owned.py [输出路径.mp4]
      python3 owned.py --times            只合成旁白，打印每一拍念多久
      python3 owned.py --stills 5 11.5    把这几个时刻的画面存成 build/stills/*.png
"""
import math
import os
import subprocess
import sys
import tomllib

import imageio_ffmpeg
import numpy as np
from PIL import ImageDraw, ImageFont

import audio
from engine import (FPS, INK, PAPER, W, Timeline, arrow, circle, cloud, ellipse, font_path,
                    rect, render_film, rounded_rect, scene_frames, speech_bubble, text_width)
from figures import alarm_clock, car, man, upper_body, woman

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
with open(os.path.join(HERE, "owned.toml"), "rb") as _f:
    SCRIPT = tomllib.load(_f)
SETTINGS = SCRIPT["settings"]
BEATS = [k for k in SCRIPT if k != "settings"]

LEAD = 0.12             # 每一拍开始后多久开口
TAIL = 0.15             # 每一拍结束前至少留这么多空
MAX_TEMPO = 1.15        # 念不完时最多提速到这个倍数，再快就该删字了
FADE = 0.25             # 节奏快：场景之间快速淡出
PEN_LEAVES = 0.3
PUNCT = "，。；：、？！“”"
SUB_Y, SUB_SIZE = 664, 34   # 底部字幕；画面里的东西都画在 y < 630 以内


# ---------------------------------------------------------------- 旁白

def synth(name, text, voice):
    """按句合成一拍的旁白。返回 (音频, [(句子, 起, 止), ...])，时间从开口算起。缓存在 build/。"""
    path = os.path.join(BUILD, f"owned_{voice.key}_{name}.npz")
    if os.path.exists(path):
        d = np.load(path)
        if str(d["text"]) == text:
            return d["x"], list(zip(audio._split(text), d["t0"], d["t1"]))
    pieces, spans, t = [], [], 0.0
    for sent in audio._split(text):
        x, sr = voice.say(sent)
        x = audio.resample_poly(x, audio.SR, sr) if sr != audio.SR else x
        x = audio._trim(x)
        spans.append((sent, t, t + len(x) / audio.SR))
        t += len(x) / audio.SR + audio.SENTENCE_GAP
        pieces += [x, np.zeros(int(audio.SENTENCE_GAP * audio.SR), np.float32)]
    x = np.concatenate(pieces[:-1]).astype(np.float32)
    x = 0.89 * x / (np.abs(x).max() + 1e-9)
    os.makedirs(BUILD, exist_ok=True)
    np.savez(path, x=x, text=text, t0=[s[1] for s in spans], t1=[s[2] for s in spans])
    return x, spans


def narration():
    """每一拍的旁白，念不完就提速。返回 {拍名: (开口时刻, 音频, 句子时间表, 提速倍数)}，全片时间。"""
    voice = audio.get_voice(SETTINGS)
    out = {}
    for name in BEATS:
        t0, t1 = SCRIPT[name]["time"]
        x, spans = synth(name, SCRIPT[name]["narration"], voice)
        start = t0 + (0.05 if t0 == 0 else LEAD)
        k = max(1.0, len(x) / audio.SR / (t1 - TAIL - start))
        if k > 1.0:
            x = audio._atempo(x, audio.SR, k)
        if k > MAX_TEMPO:
            print(f"注意：{name} 的旁白太长，要提速到 {k:.2f} 倍才念得完，建议删几个字")
        out[name] = (start, x, [(s, start + a / k, start + e / k) for s, a, e in spans], k)
    return out


NARR = {}


def hanzi(s):
    return sum(ch not in PUNCT for ch in s)


def clauses(name):
    """一拍旁白按逗号、分号、冒号切成小句，句内按字数分配时间。返回 [(小句, 起, 止)]，全片时间。"""
    out = []
    for sent, a, b in NARR[name][2]:
        parts, cur = [], ""
        for ch in sent:
            cur += ch
            if ch in "，；：。？！":
                parts.append(cur)
                cur = ""
        if cur:
            parts.append(cur)
        n, t = sum(hanzi(p) for p in parts), a
        for p in parts:
            d = (b - a) * hanzi(p) / n
            out.append((p, t, t + d))
            t += d
    return out


# ---------------------------------------------------------------- 字幕

def subtitle_chunks():
    """把小句合并成字幕条：同一句里相邻的短句合在一起，不超过 12 个字。"""
    chunks = []
    for name in BEATS:
        cur = None
        for p, a, b in clauses(name):
            if cur and hanzi(cur[0]) + hanzi(p) <= 12 and cur[0][-1] not in "。？！":
                cur = (cur[0] + p, cur[1], b)
            else:
                if cur:
                    chunks.append(cur)
                cur = (p, a, b)
        chunks.append(cur)
    out = []
    for i, (txt, a, b) in enumerate(chunks):
        nxt = chunks[i + 1][1] if i + 1 < len(chunks) else b + 0.5
        out.append((txt.rstrip("，。；：、"), a, nxt if nxt - b < 0.6 else b + 0.3))
    return out


def subtitle_overlay(chunks):
    font = ImageFont.truetype(font_path(), SUB_SIZE)

    def draw(frame, t):
        for txt, a, b in chunks:
            if a <= t < b:
                d = ImageDraw.Draw(frame)
                x = (W - d.textlength(txt, font=font)) / 2
                d.text((x, SUB_Y), txt, font=font, fill=PAPER, stroke_width=5, stroke_fill=PAPER)
                d.text((x, SUB_Y), txt, font=font, fill=INK, stroke_width=1, stroke_fill=INK)
                return
    return draw


# ---------------------------------------------------------------- 画画的小工具

def fit(txt, size, max_w):
    """文字太长时缩小字号，保证不超过 max_w 像素。"""
    while size > 12 and text_width(txt, size) > max_w:
        size -= 1
    return size


class Scene:
    """一块白板，从全片 t0 画到 t1。cl() 把某一拍第 i 个小句的时间换成白板上的时间。"""

    def __init__(self, t0, t1, fade=FADE):
        self.t0 = t0
        self.tl = Timeline(duration=t1 - t0)
        self.tl.fade, self.tl.pen_leaves = fade, PEN_LEAVES

    def cl(self, name, i):
        # 文案改动后小句变少了，就跟着最后一个小句画
        cs = clauses(name)
        _, a, b = cs[min(i, len(cs) - 1)]
        return a - self.t0, b - self.t0

    def draw(self, a, b, strokes, width=4.0, gap=0.03):
        return self.tl.strokes(a, max(b, a + 0.08), strokes, width=width, gap=gap)

    def write(self, a, b, x, y, txt, size, max_w=1180, center=False):
        b = max(b, a + 0.08)
        size = fit(txt, size, max_w)
        if center:
            return self.tl.text_center(a, b, x, y, txt, size)
        return self.tl.text(a, b, int(x), y, txt, size)


def rot_ellipse(cx, cy, rx, ry, ang, n=16):
    ca, sa = math.cos(ang), math.sin(ang)
    return [(cx + rx * math.cos(2 * math.pi * i / n) * ca - ry * math.sin(2 * math.pi * i / n) * sa,
             cy + rx * math.cos(2 * math.pi * i / n) * sa + ry * math.sin(2 * math.pi * i / n) * ca)
            for i in range(n + 1)]


def chain(x0, y0, x1, y1, link=18):
    """一串铁链：正面的椭圆环和侧面的短线交替。"""
    ang = math.atan2(y1 - y0, x1 - x0)
    n = max(2, int(math.hypot(x1 - x0, y1 - y0) / (link * 0.95)))
    out = []
    for i in range(n):
        f = (i + 0.5) / n
        cx, cy = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
        if i % 2 == 0:
            out.append(rot_ellipse(cx, cy, link * 0.62, link * 0.36, ang))
        else:
            d = link * 0.62
            out.append([(cx - d * math.cos(ang), cy - d * math.sin(ang)),
                        (cx + d * math.cos(ang), cy + d * math.sin(ang))])
    return out


def star(cx, cy, R):
    return [[(cx + (R if i % 2 == 0 else R * 0.45) * math.sin(math.pi * i / 5),
              cy - (R if i % 2 == 0 else R * 0.45) * math.cos(math.pi * i / 5)) for i in range(11)]]


def rays(cx, cy, r0, r1, n=8, a0=0.0):
    return [[(cx + r0 * math.cos(a0 + 2 * math.pi * i / n), cy + r0 * math.sin(a0 + 2 * math.pi * i / n)),
             (cx + r1 * math.cos(a0 + 2 * math.pi * i / n), cy + r1 * math.sin(a0 + 2 * math.pi * i / n))]
            for i in range(n)]


def flame(x, y, w, h, sway=0.0):
    return [[(x - w, y), (x - w * 0.9, y - h * 0.35), (x - w * 0.4, y - h * 0.68), (x + sway, y - h),
             (x + w * 0.3, y - h * 0.6), (x + w * 0.9, y - h * 0.33), (x + w, y)]]


def tag(x0, y0, x1, y1, flip=False):
    """商品吊牌：一头切掉两个角，带一个穿绳的孔（flip=True 时孔在右边）。返回 (笔画, 孔的位置)。"""
    ym = (y0 + y1) / 2
    if flip:
        hole = (x1 - 20, ym)
        shape = [(x1 - 26, y0), (x0, y0), (x0, y1), (x1 - 26, y1), (x1, ym), (x1 - 26, y0 - 1.5)]
    else:
        hole = (x0 + 20, ym)
        shape = [(x0 + 26, y0), (x1, y0), (x1, y1), (x0 + 26, y1), (x0, ym), (x0 + 26, y0 - 1.5)]
    return [shape, circle(*hole, 6)], hole


def envelope(x, y, w=56, h=36):
    return [rect(x, y, x + w, y + h), [(x, y), (x + w / 2, y + h * 0.58), (x + w, y)]]


def money_bag(cx, cy):
    return [ellipse(cx, cy, 30, 32, -60, 240),
            [(cx - 15, cy - 28), (cx - 10, cy - 36), (cx + 10, cy - 36), (cx + 15, cy - 28)],
            [(cx - 10, cy - 36), (cx - 18, cy - 50), (cx, cy - 42), (cx + 18, cy - 50), (cx + 10, cy - 36)]]


def cowboy(x, y, s=1.0):
    """戴牛仔帽、别警长星的人。"""
    P = lambda *pts: [(x + px * s, y + py * s) for px, py in pts]
    out = man(x, y, s, hat=False, arm="down")
    out += [P(*ellipse(0, -146, 34, 8, 0, 180)),
            P((-34, -150), (-26, -146), (26, -146), (34, -150)),
            P((-14, -147), (-12, -165), (-5, -170), (0, -165), (5, -170), (12, -165), (14, -147))]
    out += star(x + 9 * s, y - 95 * s, 7 * s)
    return out


def dashed(x0, y0, x1, y1, dash=16, gap=12):
    L = math.hypot(x1 - x0, y1 - y0)
    out, d = [], 0.0
    while d < L:
        e = min(L, d + dash)
        out.append([(x0 + (x1 - x0) * d / L, y0 + (y1 - y0) * d / L),
                    (x0 + (x1 - x0) * e / L, y0 + (y1 - y0) * e / L)])
        d = e + gap
    return out


# ---------------------------------------------------------------- 1. 我是一个组织人（0–6 秒）

def opening():
    s, h, idn = Scene(0, 6), SCRIPT["hook"], SCRIPT["identity"]
    (a0, a1), (c0, c1) = s.cl("hook", 0), s.cl("hook", 1)
    # “我归组织所有”：前半句画人，后半句给他挂上吊牌
    b0 = (a0 + a1) / 2
    s.draw(0.0, b0, man(330, 600, 2.3) + [[(200, 602), (470, 602)]], width=4.2, gap=0.01)
    shape, hole = tag(462, 310, 770, 394)
    t = s.draw(b0, b0 + 0.3, [[(344, 340), (400, 336), hole]] + shape, width=3.5)
    t = s.write(t, t + 0.25, 506, 320, h["tag"][0], 26, 250)
    s.write(t, max(t + 0.2, a1), 506, 356, h["tag"][1], 26, 250)
    # 星条旗和年代
    fx, fy = 1010, 80
    flag = [[(fx, fy - 10), (fx, fy + 160)], rect(fx, fy, fx + 180, fy + 110), rect(fx, fy, fx + 72, fy + 55)]
    flag += [[(fx + (72 if i < 4 else 0), fy + 110 * i / 7), (fx + 180, fy + 110 * i / 7)] for i in range(1, 7)]
    flag += [circle(fx + 12 + c * 16, fy + 12 + r * 15, 2) for r in range(3) for c in range(4)]
    t = s.draw(c0, c0 + 0.45, flag, width=3.2, gap=0.005)
    s.write(t, c1 + 0.15, 1100, 268, h["era"], 36, 300, center=True)
    # 灰西装、公文包
    (d0, d1), (e0, e1), (g0, g1) = (s.cl("identity", i) for i in range(3))
    t = s.draw(d0, d0 + 0.2, arrow(205, 392, 282, 410), width=3.5)
    s.write(t, d1, 20, 352, idn["suit"], 28, 200)
    t = s.draw(e0, e0 + 0.2, arrow(484, 540, 440, 506), width=3.5)
    s.write(t, e1, 492, 524, idn["briefcase"], 28, 200)
    # 七点半的火车
    train = [[(780, 608), (1260, 608)], rect(800, 440, 1240, 578), [(812, 456), (1228, 456)]]
    train += [rect(830 + i * 78, 470, 880 + i * 78, 515) for i in range(5)]
    train += [circle(x, 590, 16) for x in (850, 910, 1130, 1190)]
    train += [[(700, 470 + i * 30), (770, 470 + i * 30)] for i in range(3)]
    t = s.draw(g0, g0 + 0.75, train, width=3.6, gap=0.01)
    hr, mn = math.radians(7.5 * 30), math.radians(180)
    clock = [[(880, 230), (880, 262)], circle(880, 300, 36),
             [(880, 300), (880 + 20 * math.sin(hr), 300 - 20 * math.cos(hr))],
             [(880, 300), (880 + 30 * math.sin(mn), 300 - 30 * math.cos(mn))]]
    t = s.draw(t, t + 0.3, clock, width=3.6)
    s.write(t, g1 + 0.1, 880, 342, idn["watch"], 26, 120, center=True)
    return s.tl


# ---------------------------------------------------------------- 2. 比尔、琼斯家、我的考评（6–12 秒）

def competition():
    s, c = Scene(6, 12), SCRIPT["competition"]
    cl = [s.cl("competition", i) for i in range(6)]
    # ① 晋升公告
    t = s.draw(cl[0][0], cl[0][0] + 0.55, [rect(60, 110, 380, 380), [(100, 380), (100, 470)],
                                            [(340, 380), (340, 470)]], width=4)
    t = s.write(t, t + 0.4, 220, 124, c["board"][0], 34, 300, center=True)
    t = s.draw(t, t + 0.1, [[(110, 172), (330, 172)]], width=3)
    t = s.write(max(t, cl[1][0]), cl[1][0] + 0.55, 220, 200, c["board"][1], 30, 300, center=True)
    s.draw(t, cl[1][1], arrow(220, 350, 220, 262, head=22), width=6)
    # ② 琼斯家的新车，下面一行俗语解释
    a, b = cl[2]
    t = s.draw(a, b - 0.3, car(500, 420, 2.1) + [[(470, 422), (810, 422)]], width=3.8, gap=0.01)
    bow = [ellipse(616, 284, 20, 11), ellipse(658, 284, 20, 11), circle(637, 285, 5),
           [(633, 290), (624, 308)], [(641, 290), (650, 308)]]
    s.draw(t, b, bow, width=3.5, gap=0.01)
    a, b = cl[3]
    t = s.draw(a, a + 0.15, [[(642, 282), (700, 262)], rect(700, 246, 772, 278)], width=3)
    t = s.write(t, t + 0.15, 736, 248, c["car_tag"], 22, 64, center=True)
    s.write(t, b, 640, 500, c["joneses"], 24, 900, center=True)
    # ③ 我的考评：中等。场景最后半秒在淡出，所以“我的考评”一开口就写“中等”
    a, b = cl[4]
    t = s.draw(cl[3][1] - 0.1, a, [rect(900, 100, 1220, 420), [(930, 160), (1190, 160)],
                                   [(930, 210), (1190, 210)], [(930, 250), (1100, 250)]], width=3.6, gap=0.01)
    t = s.write(t, t + 0.2, 1060, 114, c["review"][0], 30, 280, center=True)
    t = s.write(t, t + 0.3, 1060, 280, c["review"][1], 56, 280, center=True)
    t = s.draw(t, t + 0.25, [rot_ellipse(1060, 318, 92, 46, -0.08, 24)], width=4.5)
    s.draw(t + 0.05, t + 0.4, upper_body(1150, 600, 0.9) + [
        [(1186, 506), (1191, 520), (1186, 525), (1181, 520), (1186, 506)],
        [(1200, 530), (1205, 544), (1200, 549), (1195, 544), (1200, 530)]], width=3.4, gap=0.01)
    return s.tl


# ---------------------------------------------------------------- 3. 走不掉：账单、养老金、人情网（12–30 秒）

def binds():
    s = Scene(12, 30)
    # 叹气
    f = [s.cl("fed_up", i) for i in range(2)]
    t = s.draw(0.1, f[0][1] - 0.3, man(600, 590, 2.1, arm="down") + [[(380, 592), (820, 592)]],
               width=4, gap=0.01)
    s.draw(t, f[1][1], [cloud(712, 318, 28, 15, 7), [(634, 326), (676, 320)]], width=3)
    # ① 分期账单：脚踝上的铁球
    b, c = [s.cl("bind_bills", i) for i in range(4)], SCRIPT["bind_bills"]
    t = s.draw(b[0][0], b[0][0] + 0.5, chain(556, 584, 410, 552) + [circle(360, 540, 50)], width=3.4, gap=0.01)
    s.write(t, b[0][1], 360, 526, c["ball"], 24, 96, center=True)
    icons = [[(86, 404), (125, 372), (164, 404)], rect(94, 404, 156, 440), rect(116, 416, 134, 440)]
    icons += car(180, 440, 0.55)
    icons += [rect(290, 366, 334, 440), [(290, 394), (334, 394)], [(326, 374), (326, 386)],
              [(326, 404), (326, 422)]]
    t = s.draw(b[1][0], b[1][1] - 0.2, icons, width=3.4, gap=0.01)
    s.draw(t, b[1][1], arrow(240, 456, 318, 502), width=3)
    t = s.draw(b[2][0], b[2][0] + 0.35, [rect(80, 140, 250, 300), [(80, 186), (250, 186)],
                                          [(122, 126), (122, 146)], [(208, 126), (208, 146)]], width=3.6)
    t = s.write(t, t + 0.3, 165, 152, c["calendar"], 24, 160, center=True)
    s.write(t, b[2][1], 165, 196, "1", 80, 160, center=True)
    t = s.draw(b[3][0], b[3][0] + 0.6, envelope(286, 206) + envelope(372, 290) +
               [[(262, 214), (280, 214)], [(262, 226), (280, 226)], [(348, 298), (366, 298)],
                [(348, 310), (366, 310)]], width=3.2, gap=0.02)
    s.draw(t, b[3][1], arrow(436, 330, 520, 430), width=3)
    # ② 养老金：手铐连着保险箱
    p, c = [s.cl("bind_pension", i) for i in range(3)], SCRIPT["bind_pension"]
    t = s.draw(p[0][0] - 0.1, p[0][0] + 0.55, [circle(658, 462, 13)] + chain(671, 464, 878, 470) +
               [circle(884, 470, 9)], width=3.4, gap=0.01)
    safe = [rect(892, 360, 1090, 540), circle(990, 450, 30), [(990, 420), (990, 430)],
            [(1020, 450), (1010, 450)], [(990, 480), (990, 470)], [(960, 450), (970, 450)],
            [(990, 450), (1006, 436)], [(1060, 430), (1060, 470)]]
    t = s.draw(t, t + 0.45, safe, width=3.6, gap=0.01)
    s.write(t, p[1][0] + 0.4, 990, 314, c["cuff"], 34, 200, center=True)
    t = s.draw(p[1][0] + 0.4, p[1][1], [rect(860, 565, 1100, 595)] +
               [[(860 + 12 * i, 565), (860 + 12 * i, 595)] for i in range(1, 20)], width=2.8, gap=0.005)
    fill = [[(860 + 12 * i + 2, 593), (860 + 12 * i + 10, 567)] for i in range(9)]
    t = s.draw(p[2][0], p[2][0] + 0.6, fill, width=4, gap=0.01)
    s.write(t, p[2][1], 1110, 562, c["progress"], 24, 160)
    # ③ 人情网：太太的牌友、孩子的玩伴，都系在公司大楼上
    q, c = [s.cl("bind_people", i) for i in range(3)], SCRIPT["bind_people"]
    bld = [rect(1100, 50, 1240, 300), rect(1110, 60, 1230, 94)]
    bld += [rect(1118 + 38 * i, 112 + 44 * j, 1142 + 38 * i, 138 + 44 * j) for i in range(3) for j in range(4)]
    t = s.draw(q[0][0] - 0.3, q[1][0] + 0.2, bld, width=3.2, gap=0.005)
    s.write(t - 0.3, t, 1170, 62, c["building"], 22, 110, center=True)
    mid = (q[1][0] + q[1][1]) / 2
    ladies = woman(440, 210, 0.55, arm="glass") + woman(500, 210, 0.55, flip=True, arm="glass")
    ladies += [rect(462, 150, 476, 168), rect(470, 146, 484, 164)]
    t = s.draw(q[1][0] + 0.2, mid, ladies, width=3, gap=0.01)
    kids = man(700, 210, 0.42, hat=False, arm="down") + man(748, 210, 0.42, hat=False, arm="down", flip=True)
    s.draw(mid, q[1][1], kids, width=3, gap=0.01)
    top = (600, 88)
    feet = [(440, 212), (500, 212), (700, 212), (748, 212)]
    heads = [(440, 128), (500, 128), (700, 146), (748, 146)]
    web = [[h, top] for h in heads]
    web += [[f, (568, 360)] for f in feet[:2]] + [[f, (632, 360)] for f in feet[2:]]
    web += [[f, (600, 240)] for f in feet]
    web += [[(430, 236), (520, 262), (600, 268), (680, 262), (770, 236)],
            [(470, 180), (600, 205), (730, 180)]]
    t = s.draw(q[2][0], q[2][1] - 0.4, web, width=2.6, gap=0.01)
    s.draw(t, q[2][1] + 0.2, [[top, (850, 80), (1100, 120)], [(748, 146), (920, 170), (1100, 200)]],
           width=2.6)
    return s.tl


# ---------------------------------------------------------------- 4. 第一次挣扎：方案进了委员会（30–40 秒）

def try1():
    s, c = Scene(30, 40), SCRIPT["try1"]
    cl = [s.cl("try1", i) for i in range(6)]
    s.draw(0.1, cl[0][1], man(170, 590, 1.9, arm="point") + [[(60, 592), (330, 592)]], width=4, gap=0.01)
    bulb = [circle(318, 296, 28), [(306, 322), (306, 342), (330, 342), (330, 322)],
            [(306, 330), (330, 330)], [(306, 336), (330, 336)],
            [(310, 318), (314, 300), (318, 310), (322, 300), (326, 318)]]
    t = s.draw(cl[2][0], cl[2][0] + 0.6, bulb, width=3.6, gap=0.01)
    t = s.draw(t, cl[2][1] - 0.2, rays(318, 296, 40, 58, 8, 0.2), width=3.2, gap=0.01)
    s.draw(t, t + 0.35, arrow(362, 268, 512, 176), width=3.5)
    # 委员会绞肉机
    grinder = [[(510, 140), (710, 140), (654, 232), (566, 232), (510, 140)],
               rect(540, 232, 680, 400), [(680, 310), (740, 310), (740, 258)], circle(740, 250, 8),
               rect(680, 358, 764, 400), [(562, 400), (542, 590)], [(658, 400), (678, 590)],
               [(500, 592), (720, 592)]]
    t = s.draw(cl[3][0] - 0.35, cl[3][0] + 0.75, grinder, width=4, gap=0.01)
    s.write(t, t + 0.4, 610, 288, c["grinder"], 34, 130, center=True)
    pages = [rect(760 + 8 * i, 92 + 8 * i, 880 + 8 * i, 192 + 8 * i) for i in range(3)]
    t = s.draw(cl[3][0] + 1.0, cl[3][1] - 0.2, pages, width=3.2, gap=0.01)
    s.write(t - 0.1, cl[3][1] + 0.1, 836, 128, c["calendar"], 28, 110, center=True)
    # 一份谁都同意、谁也记不住的报告
    t = s.draw(cl[4][0], cl[4][0] + 0.3, arrow(766, 380, 872, 430), width=3.5)
    report = [rect(880, 330, 1200, 590)] + [[(1188 - 6 * i, 342 + 4 * i), (1188 - 6 * i, 578)] for i in range(3)]
    t = s.draw(t, t + 0.8, report, width=3.8, gap=0.02)
    t = s.draw(t + 0.1, t + 0.35, [rounded_rect(930, 408, 1150, 478, 10)], width=4.5)
    s.write(t, cl[4][1], 1040, 418, c["stamp"], 36, 200, center=True)
    lines = [[(920, 512 + 26 * i), (1120 - 40 * (i % 2), 512 + 26 * i)] for i in range(3)]
    t = s.draw(cl[5][0], cl[5][0] + 0.4, lines, width=2.6, gap=0.02)
    web = [[(1200, 330), (1200 - 110 * math.cos(a), 330 + 110 * math.sin(a))]
           for a in (0.0, 0.4, 0.8, 1.2, 1.5708)]
    for r in (36, 70, 104):
        web.append([(1200 - r * math.cos(a), 330 + r * math.sin(a)) for a in (0.0, 0.4, 0.8, 1.2, 1.5708)])
    s.draw(t + 0.1, cl[5][1] - 0.1, web, width=2.4, gap=0.01)
    return s.tl


# ---------------------------------------------------------------- 5. 第二次挣扎：辞职信，和更小的座位（40–50 秒）

def try2():
    s, c = Scene(40, 50), SCRIPT["try2"]
    cl = [s.cl("try2", i) for i in range(9)]
    t = s.draw(0.1, cl[0][1], man(300, 590, 1.8, arm="letter") + [[(40, 592), (700, 592)]], width=4, gap=0.01)
    s.draw(cl[1][0], cl[1][1], [[(292, 262), (288, 236)], [(316, 262), (330, 240)], [(272, 268), (252, 252)]],
           width=3.5)
    s.write(cl[2][0], cl[2][1], 356, 436, c["letter"], 26, 100)
    door = [rect(60, 220, 190, 590), rect(70, 176, 180, 212), circle(170, 410, 5)]
    t = s.draw(cl[3][0], cl[3][0] + 0.7, door, width=4, gap=0.01)
    s.write(t, cl[3][1], 125, 180, c["door"], 26, 100, center=True)
    # 经理笑着拍拍我
    t = s.draw(cl[4][0], cl[4][1] + 0.2, man(500, 590, 1.9, flip=True, hat=False, arm="point") +
               [ellipse(492, 343, 12, 6, 20, 160)], width=4, gap=0.01)
    s.draw(cl[5][0], cl[5][1], [ellipse(367, 343, 26, 26, 250, 300), ellipse(367, 343, 38, 38, 255, 295)],
           width=3)
    t = s.draw(cl[6][0], cl[6][0] + 0.4, speech_bubble(430, 158, 690, 226, (500, 282)), width=3.6)
    s.write(t, cl[6][1], 560, 172, c["boss"], 32, 240, center=True)
    # 画面一转：靠窗的座位没了，换到柱子后面
    old = dashed(745, 100, 745, 600) + [rect(800, 110, 960, 222), [(880, 110), (880, 222)],
                                         [(800, 166), (960, 166)], [(790, 300), (970, 300)],
                                         [(802, 300), (802, 380)], [(958, 300), (958, 380)]]
    t = s.draw(cl[6][1] - 0.2, cl[7][0], old, width=3.6, gap=0.01)
    s.write(t - 0.3, cl[7][0] + 0.2, 880, 316, c["seat_old"], 26, 120, center=True)
    t = s.draw(cl[7][0] + 0.1, cl[7][1], [[(778, 96), (990, 400)], [(990, 96), (778, 400)]], width=5.5)
    a, b = cl[8]
    t = s.draw(a, a + 0.2, arrow(976, 300, 1032, 372), width=3.5)
    t = s.draw(t, t + 0.3, [[(1040, 60), (1040, 592)], [(1080, 60), (1080, 592)]], width=4)
    t = s.write(t, t + 0.3, 1092, 96, c["seat_new"], 24, 170)
    tiny = [[(1100, 480), (1176, 480)], [(1104, 480), (1104, 540)], [(1172, 480), (1172, 540)]]
    t = s.draw(t, t + 0.55, tiny + upper_body(1138, 480, 0.7), width=3.2, gap=0.01)
    sweat = [[(1166, 398), (1171, 412), (1166, 417), (1161, 412), (1166, 398)]]
    squeeze = [[(1214, 340), (1214, 592)]] + arrow(1262, 440, 1222, 440, 12) + \
        arrow(1138, 330, 1138, 392, 12) + sweat
    s.draw(t, b - 0.05, squeeze, width=3.4, gap=0.01)
    return s.tl


# ---------------------------------------------------------------- 6. 认命：儿子的作文，壁炉（50–60 秒）

def acceptance():
    s, c = Scene(50, 60), SCRIPT["acceptance"]
    cl = [s.cl("acceptance", i) for i in range(7)]
    moon = ellipse(70, 70, 22, 22, 60, 300) + ellipse(80, 70, 17, 20, 270, 90)[::-1]
    s.draw(cl[0][0], cl[0][1], [moon], width=3.5)
    paper = [rect(90, 110, 600, 590)] + [[(110, y), (580, y)] for y in (186, 246, 306)]
    t = s.draw(cl[1][0], cl[1][1], paper, width=3.4, gap=0.01)
    s.write(cl[2][0], cl[2][1], 345, 126, c["essay"][0], 42, 460, center=True)
    t = s.write(cl[3][0], cl[3][1] - 0.2, 130, 200, c["essay"][1], 36, 440)
    kid_man = man(220, 560, 0.95, arm="briefcase")
    s.draw(t, cl[3][1] + 0.3, kid_man, width=3.2, gap=0.01)
    t = s.write(cl[4][0], cl[4][0] + 0.7, 130, 260, c["essay"][2], 36, 440)
    building = [rect(310, 350, 420, 562)] + [rect(326 + 30 * i, 368 + 40 * j, 346 + 30 * i, 390 + 40 * j)
                                             for i in range(3) for j in range(4)]
    building += [circle(520, 400, 26)] + rays(520, 400, 34, 50, 8) + [[(110, 564), (580, 560)]]
    t = s.draw(t, cl[4][1] + 0.2, building, width=3, gap=0.01)
    # 壁炉
    place = [rect(690, 230, 1190, 262), [(720, 262), (720, 592)], [(1160, 262), (1160, 592)],
             [(690, 592), (1190, 592)], [(810, 592), (810, 420)], ellipse(940, 420, 130, 90, 180, 360),
             [(1070, 420), (1070, 592)], rect(840, 562, 1040, 588), [(870, 562), (1010, 588)]]
    t = s.draw(t, cl[5][0] + 0.2, place, width=3.8, gap=0.01)
    t = s.draw(cl[5][0] + 0.2, cl[5][0] + 0.5, [rect(980, 110, 1110, 180)], width=3.4)
    s.write(t, cl[5][1], 1045, 128, "辞职信", 26, 110, center=True)
    t = s.draw(cl[6][0], cl[6][0] + 0.4, [[(1040, 186), (1010, 300), (950, 440)],
                                          [(936, 422), (950, 440), (966, 424)]], width=3.4)
    fire = flame(900, 560, 26, 70, -8) + flame(950, 560, 34, 110, 6) + flame(1000, 560, 24, 76, 10)
    t = s.draw(t, cl[6][1], fire, width=3.6, gap=0.02)
    s.draw(t + 0.1, t + 1.0, flame(870, 560, 22, 50, -6) + flame(940, 560, 46, 150, -4) +
           flame(1030, 560, 22, 56, 8), width=3.2, gap=0.02)
    return s.tl


# ---------------------------------------------------------------- 7. 幻想：大富翁、孤胆英雄、我不同意（60–70 秒）

def dream():
    s, c = Scene(60, 70), SCRIPT["dream"]
    cl = [s.cl("dream", i) for i in range(7)]
    chair = [[(80, 520), (74, 372), (104, 348), (186, 348), (216, 372), (210, 520)],
             [(60, 520), (60, 478), (92, 468)], [(230, 520), (230, 478), (198, 468)],
             [(60, 520), (230, 520)], [(70, 520), (70, 560)], [(220, 520), (220, 560)]]
    t = s.draw(0.05, cl[0][0] + 0.45, chair + upper_body(145, 520, 1.4), width=3.6, gap=0.01)
    t = s.draw(t, t + 0.2, [circle(226, 400, 6), circle(252, 368, 10), circle(286, 330, 14)], width=3)
    # 云的形状决定了三个画面能放多大：越靠上下边，云越窄
    s.draw(t, cl[0][1], [cloud(775, 300, 460, 250, 26)], width=3.6)
    # ① 小店开成全美连锁，大富翁
    a, b = cl[1]
    shop = [rect(440, 168, 640, 218), rect(452, 238, 628, 380), rect(515, 300, 565, 380)]
    shop += [ellipse(463 + 22 * i, 238, 11, 11, 0, 180) for i in range(8)] + [[(452, 238), (628, 238)]]
    t = s.draw(a, b - 0.4, shop, width=3.4, gap=0.01)
    s.write(t, b, 540, 180, c["shop"], 22, 190, center=True)
    a, b = cl[2]
    bags = [(480, 418), (600, 418), (540, 404)]
    t = s.draw(a, b - 0.35, sum((money_bag(x, y) for x, y in bags), []), width=3.4, gap=0.01)
    for x, y in bags:
        s.write(t, t + 0.12, x, y - 18, "$", 30, 40, center=True)
        t += 0.1
    # ② 一路向西，孤胆英雄
    a, b = cl[3]
    west = [[(676, 440), (900, 440)], circle(880, 232, 26)] + rays(880, 232, 34, 48, 8)
    west += [[(712, 440), (712, 352)], [(712, 400), (692, 398), (692, 370)], [(712, 384), (732, 382), (732, 358)]]
    s.draw(a, b, west, width=3.4, gap=0.01)
    a, b = cl[4]
    t = s.draw(a, b - 0.45, cowboy(800, 440, 1.2), width=3.6, gap=0.01)
    s.write(t, b, 800, 150, c["hero"], 30, 200, center=True)
    # ③ 在会上站起来说：我不同意
    a, b = cl[5]
    meet = [[(925, 430), (1095, 430)], [(937, 430), (937, 462)], [(1083, 430), (1083, 462)]]
    meet += man(1010, 430, 1.0, hat=False, arm="point") + upper_body(955, 430, 0.55) + upper_body(1066, 430, 0.55)
    s.draw(a, b, meet, width=3.2, gap=0.01)
    a, b = cl[6]
    t = s.draw(a, a + 0.3, speech_bubble(910, 190, 1110, 248, (1010, 292)), width=3.6)
    t = s.write(t, b, 1010, 201, c["speech"], 32, 180, center=True)
    s.write(t, t + 0.25, 932, 330, "!", 30, 30)
    s.write(t + 0.2, t + 0.45, 1092, 340, "!", 30, 30)
    return s.tl


# ---------------------------------------------------------------- 8. 闹钟响了（70–75 秒）

def ending():
    s, c = Scene(70, 75, fade=0.4), SCRIPT["ending"]
    cl = [s.cl("ending", i) for i in range(4)]
    t = s.draw(0.0, cl[0][1] - 0.2, alarm_clock(190, 250, 64, 6.5), width=4, gap=0.01)
    ring = [ellipse(190, 250, 92, 92, 200, 240), ellipse(190, 250, 110, 110, 205, 235),
            ellipse(190, 250, 92, 92, -60, -20), ellipse(190, 250, 110, 110, -55, -25)]
    t = s.draw(t, cl[0][1], ring, width=3.4, gap=0.01)
    s.draw(t, t + 0.25, [circle(470, 70, 8), circle(530, 48, 12), circle(600, 80, 6), circle(700, 56, 10),
                         circle(770, 86, 7)], width=3)
    # 镜子里系领带的他，脖子上还挂着吊牌
    P = lambda *pts: [(640 + px * 2.4, 520 + py * 2.4) for px, py in pts]
    body = [P(*circle(0, -84, 14)), P(*ellipse(0, -90, 14, 9, 190, 350)),
            P((-5, -70), (-5, -63)), P((5, -70), (5, -63)),
            P((-22, -58), (-6, -63)), P((6, -63), (22, -58)),
            P((-22, -58), (-19, 0)), P((22, -58), (19, 0)), P((-6, -63), (0, -46), (6, -63)),
            P((0, -60), (-3, -46), (0, -28), (3, -46), (0, -60)),
            P((-22, -56), (-36, -28), (-6, -56)), P((22, -56), (36, -28), (6, -56))]
    a = cl[1][0]
    t = s.draw(a - 0.1, a + 0.35, [ellipse(640, 360, 150, 232), [(640, 128), (640, 100)]], width=4)
    t = s.draw(t, cl[2][1] - 0.2, body, width=3.8, gap=0.01)
    shape, hole = tag(220, 430, 440, 500, flip=True)
    t = s.draw(t, t + 0.3, [[(626, 372), (520, 430), (hole[0] + 6, hole[1])]] + shape, width=3.2)
    s.write(t, t + 0.25, 234, 438, SCRIPT["hook"]["tag"][0], 20, 170)
    s.write(t + 0.2, t + 0.45, 234, 468, SCRIPT["hook"]["tag"][1], 20, 170)
    # 留白，一个大问号
    a, b = cl[3]
    t = s.write(a, a + 0.8, 1010, 110, "？", 250, 300)
    src = SETTINGS["source"]
    s.write(t, t + 0.4, int(1250 - text_width(src, 20)), 600, src, 20, 600)
    return s.tl


SCENES = [opening, competition, binds, try1, try2, acceptance, dream, ending]


# ---------------------------------------------------------------- 声音

def alarm_ring(dur=0.85):
    """闹钟铃：两个高音交替，被小锤每秒敲十八下。"""
    t = np.arange(int(dur * audio.SR)) / audio.SR
    gate = audio.smooth_env((np.sin(2 * np.pi * 18 * t) > 0).astype(float), 4)
    tone = np.sin(2 * np.pi * 2400 * t) + 0.6 * np.sin(2 * np.pi * 3150 * t) + 0.3 * np.sin(2 * np.pi * 4700 * t)
    env = np.minimum(1, t / 0.01) * np.clip((dur - t) / 0.15, 0, 1)
    return 0.2 * tone * gate * env


def soundtrack(scenes, wav_path):
    offsets = list(np.cumsum([0] + [tl.duration for tl in scenes[:-1]]))
    total = sum(tl.duration for tl in scenes)
    voice = np.zeros(int(total * audio.SR) + audio.SR)
    for name in BEATS:
        start, x, _, _ = NARR[name]
        i = int(start * audio.SR)
        voice[i:i + len(x)] += x
    ring = alarm_ring()
    i = int(SCRIPT["ending"]["time"][0] * audio.SR)
    voice[i:i + len(ring)] += ring
    print("合成配乐和马克笔声……", flush=True)
    mixed = audio.mix(voice, audio.music_track(total), audio.marker_track(scenes, offsets, total),
                      total, fade_out=5.0)
    audio.write_wav(wav_path, mixed)


def stills(scenes, times):
    """把几个时刻的画面存成 png（含字幕），用来检查构图。"""
    out_dir = os.path.join(BUILD, "stills")
    os.makedirs(out_dir, exist_ok=True)
    overlay = subtitle_overlay(subtitle_chunks())
    offsets = list(np.cumsum([0] + [tl.duration for tl in scenes]))
    for t in times:
        i = max(k for k in range(len(scenes)) if offsets[k] <= t)
        target = int(round((t - offsets[i]) * FPS))
        for f, frame in enumerate(scene_frames(scenes[i])):
            if f == target:
                overlay(frame, t)
                path = os.path.join(out_dir, f"{t:05.1f}.png")
                frame.save(path)
                print(path)
                break


if __name__ == "__main__":
    NARR.update(narration())
    if "--times" in sys.argv:
        for name in BEATS:
            start, x, spans, k = NARR[name]
            print(f"{name:13s} {SCRIPT[name]['time']}  念完 {start + len(x) / audio.SR:5.2f}  提速 {k:.2f}")
        sys.exit()
    scenes = [fn() for fn in SCENES]
    if "--stills" in sys.argv:
        stills(scenes, [float(a) for a in sys.argv[sys.argv.index("--stills") + 1:]])
        sys.exit()
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "organization_man_owned.mp4")
    print(f"总时长 {sum(tl.duration for tl in scenes):.1f} 秒")
    silent = os.path.join(BUILD, "owned_video_only.mp4")
    wav = os.path.join(BUILD, "owned_soundtrack.wav")
    soundtrack(scenes, wav)
    overlay = subtitle_overlay(subtitle_chunks()) if SETTINGS.get("subtitles", True) else None
    render_film(scenes, silent, overlay)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                    "-i", silent, "-i", wav, "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", out], check=True)
    print("完成：", out)

"""《组织人的一天》：白板手绘动画，约 3 分钟。

1956 年，芝加哥郊外帕克森林的一个上班族，从清晨出门到深夜入睡。
灵感来自威廉·H·怀特《组织人》（The Organization Man, 1956）。

用法: python3 day.py [输出路径.mp4] [场景编号...]
      例：python3 day.py preview.mp4 3   只渲染第 3 个场景
"""
import math
import os
import sys
import tomllib

from engine import (Timeline, arrow, check, circle, cloud, ellipse, rect,
                    render_film, rounded_rect, speech_bubble, text_width)
from figures import (alarm_clock, briefcase, car, house, man, reader, upper_body,
                     woman)


HERE = os.path.dirname(os.path.abspath(__file__))
# 所有文案（旁白、字幕、画面里的字）都在 script.toml 里
with open(os.path.join(HERE, "script.toml"), "rb") as _f:
    SCRIPT = tomllib.load(_f)


def per_char(txt, rate=0.11):
    return max(0.6, len(txt) * rate)


def fit(txt, size, max_w):
    """文字太长时缩小字号，保证不超过 max_w 像素。"""
    while size > 12 and text_width(txt, size) > max_w:
        size -= 1
    return size


def caption(tl, t0, line, txt, rate=0.11):
    """顶部字幕：第 0 行是时间地点（大字），第 1、2 行是说明。返回结束时间。"""
    x, y, size = [(60, 34, 40), (60, 94, 30), (60, 138, 30)][line]
    return tl.text(t0, t0 + per_char(txt, rate), x, y, txt, fit(txt, size, 1160))


def captions(tl, t, lines, first_gap=0.3, gap=0.1):
    """依次写出说明字幕（第 1 行起）。"""
    for i, txt in enumerate(lines):
        t = caption(tl, t + (first_gap if i == 0 else gap), i + 1, txt)
    return t


def label(tl, t0, t1, x, y, txt, size, max_w):
    return tl.text(t0, t1, x, y, txt, fit(txt, size, max_w))


# ---------------------------------------------------------------- 片头

def title_card():
    tl, c = Timeline(), SCRIPT["title_card"]
    t = tl.text_center(0.4, 2.4, 640, 250, c["title"], fit(c["title"], 76, 1100))
    t = tl.strokes(t + 0.1, t + 0.8, [[(420, 352), (640, 346), (860, 352)]], width=5)
    tl.text_center(t + 0.2, t + 1.6, 640, 380, c["subtitle"], fit(c["subtitle"], 36, 1100))
    return tl


# ---------------------------------------------------------------- 1. 清晨出门

def morning():
    tl, c = Timeline(), SCRIPT["morning"]
    t = caption(tl, 0.3, 0, c["captions"][0])
    xs = [40, 440, 840]
    t = tl.strokes(t + 0.2, t + 0.8, [[(30, 480), (1250, 480)]], width=4)
    # 第一栋房子画得慢，后两栋越来越快——标准化的郊区
    for x0, d in zip(xs, [4.0, 2.2, 1.6]):
        t = tl.strokes(t + 0.1, t + d, house(x0, 480), width=4, gap=0.05)
    for x0 in xs:
        t = tl.strokes(t + 0.1, t + 1.0, car(x0 + 262, 480, 0.9), width=3.5, gap=0.02)
    t = tl.strokes(t + 0.1, t + 0.6, [[(30, 662), (1250, 662)]], width=4)
    for i, x0 in enumerate(xs):
        d = [2.2, 1.4, 1.1][i]
        t = tl.strokes(t + 0.1, t + d, man(x0 + 175, 660, 0.9), width=3.8, gap=0.02)
        t = tl.strokes(t + 0.05, t + d * 0.8, woman(x0 + 70, 660, 0.85), width=3.8, gap=0.02)
    captions(tl, t, c["captions"][1:])
    return tl


# ---------------------------------------------------------------- 2. 通勤火车

def train():
    tl, c = Timeline(), SCRIPT["train"]
    t = caption(tl, 0.3, 0, c["captions"][0])
    t = tl.strokes(t + 0.2, t + 1.6, [
        rect(40, 200, 1240, 620),
        [(40, 600), (1240, 600)],
        [(60, 232), (1220, 232)],
    ], width=4.5)
    xs = [170, 400, 630, 860, 1090]
    wins = [rect(x - 72, 262, x + 72, 352) for x in xs]
    t = tl.strokes(t + 0.1, t + 1.6, wins, width=3.5, gap=0.04)
    straps = []
    for x in [285, 515, 745, 975]:
        straps += [[(x, 232), (x, 258)], ellipse(x, 268, 8, 10, -90, 272)]
    t = tl.strokes(t + 0.1, t + 1.2, straps, width=3, gap=0.03)
    # 轮子和铁轨
    t = tl.strokes(t + 0.1, t + 1.0,
                   [circle(x, 640, 18) for x in (150, 230, 1050, 1130)] +
                   [[(20, 662), (1260, 662)]], width=4)
    for i, x in enumerate(xs):
        d = [3.0, 1.6, 1.3, 1.1, 1.0][i]
        t = tl.strokes(t + 0.1, t + d, reader(x, 600, 1.15), width=3.6, gap=0.02)
    captions(tl, t, c["captions"][1:], gap=0.3)
    return tl


# ---------------------------------------------------------------- 3. 公司大楼 8:59

def office():
    tl, c = Timeline(), SCRIPT["office"]
    xs = [700, 590, 480, 370, 260, 150]
    t = caption(tl, 0.3, 0, c["captions"][0])
    t = tl.strokes(t + 0.2, t + 1.7, [
        [(820, 640), (820, 150), (1160, 150), (1160, 640)],
        [(870, 150), (870, 95), (1110, 95), (1110, 150)],
        [(990, 95), (990, 48)], ellipse(990, 42, 6, 6),
    ], width=5)
    t = tl.strokes(t, t + 0.7, [rect(840, 160, 1140, 198)], width=4)
    t = tl.text_center(t, t + 1.2, 990, 164, c["company"], fit(c["company"], 24, 280))
    clock = [circle(990, 262, 44), [(990, 262), (990, 228)], [(990, 262), (966, 262)]]
    clock += [[(990 + 37 * math.cos(math.radians(a)), 262 + 37 * math.sin(math.radians(a))),
               (990 + 42 * math.cos(math.radians(a)), 262 + 42 * math.sin(math.radians(a)))]
              for a in range(0, 360, 90)]
    t = tl.strokes(t, t + 1.1, clock, width=4, gap=0.06)
    wins = [rect(842 + c * 62, 330 + r * 42, 888 + c * 62, 358 + r * 42)
            for r in range(5) for c in range(5)]
    t = tl.strokes(t, t + 2.2, wins, width=3, gap=0.02)
    t = tl.strokes(t, t + 0.7, [[(945, 640), (945, 565), (1035, 565), (1035, 640)],
                                [(990, 565), (990, 640)]], width=4.5)
    t = tl.strokes(t, t + 0.5, [[(40, 640), (1240, 640)]], width=4)
    t = tl.strokes(t + 0.1, t + 3.0, man(xs[0], 640), width=4, gap=0.05)
    for i, x in enumerate(xs[1:]):
        d = [1.2, 1.0, 0.85, 0.75, 0.65][i]
        t = tl.strokes(t + 0.08, t + d, man(x, 640), width=4, gap=0.015)
    label(tl, t + 0.2, t + 3.0, 60, 662, c["bottom"], 32, 1160)
    t += 3.2
    # 提线木偶：组织的横杆
    t = tl.strokes(t, t + 0.9, [rect(110, 258, 740, 296),
                                [(425, 258), (425, 222)], [(385, 222), (465, 222)]], width=5)
    t = tl.text_center(t, t + 0.7, 425, 260, c["bar"], fit(c["bar"], 28, 600))
    t = tl.strokes(t, t + 1.4, [[(x, 296), (x, 470)] for x in xs], width=2.5, gap=0.04)
    t = tl.strokes(t, t + 0.4, [[(740, 279), (790, 230), (840, 199)]], width=3.5)
    captions(tl, t, c["captions"][1:], first_gap=0.2)
    return tl


# ---------------------------------------------------------------- 4. 人格测试

def personality_test():
    tl, c = Timeline(), SCRIPT["personality_test"]
    t = caption(tl, 0.3, 0, c["captions"][0])
    # 书桌和答题的人
    t = tl.strokes(t + 0.2, t + 1.2, [
        rect(180, 470, 480, 484), rect(192, 484, 468, 620), [(330, 500), (330, 604)],
        [(20, 620), (540, 620)],
    ], width=4.5)
    t = tl.strokes(t + 0.1, t + 2.6, upper_body(330, 470, 1.3, arms="pencil"),
                   width=4, gap=0.04)
    # 背后监督的人事部职员
    t = tl.strokes(t + 0.1, t + 2.0, man(95, 620, 1.2, hat=False, arm="clipboard"),
                   width=3.8, gap=0.03)
    # 放大的试卷
    t = tl.strokes(t + 0.2, t + 1.4, [
        [(360, 466), (560, 196)], [(360, 466), (560, 636)],
        rect(560, 190, 1225, 640),
    ], width=3.2)
    t = label(tl, t, t + 1.4, 590, 206, c["q1"], 32, 610)
    boxes = lambda y: [rect(592, y + 4, 618, y + 30)]
    t = tl.strokes(t + 0.1, t + 0.3, boxes(262), width=3)
    t = label(tl, t, t + 1.6, 634, 262, c["q1_a"], 28, 570)
    t = tl.strokes(t + 0.1, t + 0.3, boxes(312), width=3)
    t = label(tl, t, t + 1.8, 634, 312, c["q1_b"], 28, 570)
    t = tl.strokes(t + 0.5, t + 0.8, check(594, 316), width=5)
    t = label(tl, t + 0.4, t + 1.2, 590, 392, c["q2"], 32, 610)
    t = tl.strokes(t + 0.1, t + 0.3, boxes(446), width=3)
    t = label(tl, t, t + 1.6, 634, 446, c["q2_a"], 28, 570)
    t = tl.strokes(t + 0.1, t + 0.3, boxes(496), width=3)
    t = label(tl, t, t + 1.8, 634, 496, c["q2_b"], 28, 570)
    t = tl.strokes(t + 0.5, t + 0.8, check(594, 500), width=5)
    # 评语
    t = tl.strokes(t + 0.4, t + 0.9, [rounded_rect(900, 566, 1200, 624, 8)], width=5)
    t = tl.text_center(t, t + 1.2, 1050, 573, c["verdict"], fit(c["verdict"], 32, 270))
    captions(tl, t, c["captions"][1:], gap=0.2)
    return tl


# ---------------------------------------------------------------- 5. 委员会会议

def meeting():
    tl, c = Timeline(), SCRIPT["meeting"]
    t = caption(tl, 0.3, 0, c["captions"][0])
    t = tl.strokes(t + 0.2, t + 1.4, [
        rect(80, 470, 880, 484), rect(92, 484, 868, 560),
        [(110, 560), (110, 620)], [(850, 560), (850, 620)],
        [(20, 620), (1260, 620)],
    ], width=4.5)
    xs = [145, 270, 395, 520, 645, 770]
    for i, x in enumerate(xs):
        d = [2.2, 1.2, 1.0, 0.9, 0.8, 0.7][i]
        t = tl.strokes(t + 0.08, t + d, upper_body(x, 470), width=3.8, gap=0.02)
    # 上司和图表
    t = tl.strokes(t + 0.1, t + 2.2, man(960, 620, 1.0, hat=False, arm="point"),
                   width=4, gap=0.03)
    t = tl.strokes(t + 0.1, t + 1.6, [
        rect(1040, 300, 1230, 440),
        [(1060, 440), (1042, 620)], [(1210, 440), (1228, 620)], [(1135, 440), (1135, 620)],
        [(1060, 320), (1060, 425), (1215, 425)],
        [(1066, 412), (1100, 395), (1130, 400), (1165, 360), (1205, 330)],
    ], width=3.6, gap=0.04)
    t = tl.strokes(t + 0.2, t + 0.8, speech_bubble(800, 382, 1010, 436, (950, 462)), width=3.5)
    t = tl.text_center(t, t + 1.4, 905, 390, c["boss"], fit(c["boss"], 28, 190))
    # 每个人的回答都一样
    for i, x in enumerate(xs):
        t = tl.strokes(t + 0.15, t + 0.45, speech_bubble(x - 36, 318, x + 36, 354, (x, 370)),
                       width=3)
        t = tl.text_center(t, t + 0.4, x, 321, c["reply"], fit(c["reply"], 22, 64))
    # 他心里其实有话
    t = tl.strokes(t + 0.6, t + 1.6, [
        circle(432, 306, 4), circle(452, 290, 6),
        cloud(560, 240, 118, 32),
    ], width=3)
    t = tl.text_center(t, t + 1.4, 560, 226, c["thought"], fit(c["thought"], 26, 210))
    captions(tl, t, c["captions"][1:], first_gap=0.4, gap=0.2)
    return tl


# ---------------------------------------------------------------- 6. 后院聚会和调令

def party():
    tl, c = Timeline(), SCRIPT["party"]
    t = caption(tl, 0.3, 0, c["captions"][0])
    t = tl.strokes(t + 0.2, t + 0.6, [[(20, 640), (1260, 640)]], width=4)
    # 烤炉和烟
    t = tl.strokes(t + 0.1, t + 1.4, [
        ellipse(110, 540, 40, 30, 0, 180), [(70, 540), (150, 540)],
        [(85, 565), (70, 640)], [(135, 565), (150, 640)], [(110, 570), (110, 640)],
        [(95, 530), (88, 510), (100, 492), (92, 470)],
        [(122, 530), (130, 508), (118, 488), (126, 462)],
    ], width=3.8, gap=0.04)
    people = [(240, man, {"arm": "glass", "hat": False}), (340, woman, {"arm": "glass"}),
              (440, man, {"arm": "glass", "hat": False}), (540, woman, {"arm": "glass"})]
    for i, (x, fig, kw) in enumerate(people):
        d = [2.0, 1.4, 1.1, 1.0][i]
        t = tl.strokes(t + 0.08, t + d, fig(x, 640, 1.0, **kw), width=3.8, gap=0.02)
    # 他拿着一封信
    t = tl.strokes(t + 0.3, t + 2.0, man(690, 640, 1.0, hat=False, arm="letter"),
                   width=4, gap=0.03)
    t = tl.strokes(t + 0.2, t + 0.7, arrow(756, 512, 840, 450), width=3.5)
    t = tl.strokes(t + 0.1, t + 1.0, [rect(850, 220, 1230, 580), [(870, 290), (1210, 290)]],
                   width=4)
    # 卡片上的字：抬头、标题、正文若干行、落款（落款靠右）
    lines = c["letter"]
    t = label(tl, t, t + 1.1, 872, 248, lines[0], 24, 336)
    t = label(tl, t + 0.1, t + 0.9, 872, 304, lines[1], 36, 336)
    body = lines[2:-1]
    for i, txt in enumerate(body):
        t = label(tl, t + 0.05, t + per_char(txt, 0.12), 872, 366 + i * 42, txt, 28, 336)
    sign = lines[-1]
    sz = fit(sign, 24, 336)
    t = tl.text(t + 0.1, t + 0.8, int(1210 - text_width(sign, sz)), 520, sign, sz)
    captions(tl, t, c["captions"][1:], first_gap=0.4, gap=0.2)
    return tl


# ---------------------------------------------------------------- 7. 深夜

def night():
    tl, c = Timeline(), SCRIPT["night"]
    t = caption(tl, 0.3, 0, c["captions"][0])
    t = tl.strokes(t + 0.2, t + 0.6, [[(20, 600), (1260, 600)]], width=4)
    # 窗户和月亮
    t = tl.strokes(t + 0.1, t + 1.4, [
        rect(360, 200, 540, 340), [(450, 200), (450, 340)], [(360, 270), (540, 270)],
        ellipse(410, 236, 18, 18, 60, 300) + ellipse(420, 236, 14, 16, 270, 90)[::-1],
    ], width=3.8, gap=0.05)
    # 床和睡着的人
    t = tl.strokes(t + 0.1, t + 2.6, [
        [(90, 600), (90, 430), (110, 430), (110, 600)],
        [(110, 520), (560, 520)], [(110, 560), (560, 560)], [(560, 470), (560, 600)],
        ellipse(160, 495, 42, 20),
        circle(175, 478, 15), ellipse(175, 470, 15, 10, 200, 360),
        [(150, 505), (220, 470), (330, 466), (450, 472), (530, 480), (556, 500)],
        [(240, 478), (250, 520)],
    ], width=4, gap=0.04)
    # 床头柜和闹钟
    t = tl.strokes(t + 0.1, t + 1.0, [rect(600, 500, 700, 600), [(600, 540), (700, 540)],
                                      circle(650, 522, 3)], width=4)
    t = tl.strokes(t + 0.1, t + 1.4, alarm_clock(650, 466, 24, 6.5), width=3.5, gap=0.05)
    # 挂好的灰色法兰绒西装和礼帽
    jx, jy = 940, 230
    t = tl.strokes(t + 0.2, t + 3.0, [
        circle(jx, jy - 6, 5),
        [(jx, jy), (jx - 50, jy + 30), (jx + 50, jy + 30), (jx, jy)],
        [(jx - 50, jy + 30), (jx - 62, jy + 50), (jx - 56, jy + 190), (jx + 56, jy + 190),
         (jx + 62, jy + 50), (jx + 50, jy + 30)],
        [(jx - 14, jy + 30), (jx, jy + 100), (jx + 14, jy + 30)],
        [(jx, jy + 100), (jx, jy + 190)],
        circle(jx + 7, jy + 125, 3), circle(jx + 7, jy + 155, 3),
        [(jx - 62, jy + 50), (jx - 74, jy + 160)], [(jx + 62, jy + 50), (jx + 74, jy + 160)],
        [(1060, 214), (1060, 230)],
        [(1046, 262), (1047, 240), (1055, 233), (1060, 237), (1065, 233), (1073, 240),
         (1074, 262)],
        ellipse(1060, 264, 32, 6),
    ], width=3.8, gap=0.04)
    t = tl.strokes(t + 0.1, t + 0.8, briefcase(920, 600, 1.6), width=4)
    captions(tl, t, c["captions"][1:], gap=0.6)
    return tl


# ---------------------------------------------------------------- 片尾

def end_card():
    tl, c = Timeline(), SCRIPT["end_card"]
    t = tl.text_center(0.3, 1.8, 640, 270, c["title"], fit(c["title"], 56, 1100))
    t = tl.strokes(t + 0.3, t + 0.8, [[(470, 352), (640, 347), (810, 352)]], width=4)
    tl.text_center(t + 0.3, t + 2.4, 640, 380, c["source"], fit(c["source"], 26, 1160))
    return tl


SCENES = [title_card, morning, train, office, personality_test, meeting, party, night,
          end_card]

# 旁白在场景开始后 NARRATION_START 秒开始念，文字见 script.toml
NARRATION_START = 0.7


def build(picks):
    """生成场景、旁白，并让每个场景至少和它的旁白一样长。"""
    import audio
    scenes, voices = [], []
    for i in picks:
        fn = SCENES[i]
        tl = fn()
        voice = audio.narrate(SCRIPT[fn.__name__]["narration"], fn.__name__,
                              audio.get_voice(SCRIPT.get("settings", {})))
        tl.min_duration = NARRATION_START + len(voice) / audio.SR + 1.6
        scenes.append(tl)
        voices.append(voice)
    return scenes, voices


def soundtrack(scenes, voices, wav_path):
    import audio
    import numpy as np
    offsets = list(np.cumsum([0] + [tl.duration for tl in scenes[:-1]]))
    total = sum(tl.duration for tl in scenes)
    narr = np.zeros(int(total * audio.SR) + audio.SR)
    for off, v in zip(offsets, voices):
        i = int((off + NARRATION_START) * audio.SR)
        narr[i:i + len(v)] += v
    print("合成配乐和马克笔声……", flush=True)
    mixed = audio.mix(narr, audio.music_track(total),
                      audio.marker_track(scenes, offsets, total), total)
    audio.write_wav(wav_path, mixed)


if __name__ == "__main__":
    import subprocess
    import imageio_ffmpeg
    here = HERE
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "organization_man_day.mp4")
    picks = [int(a) for a in sys.argv[2:]] or range(len(SCENES))
    scenes, voices = build(picks)
    print(f"总时长 {sum(s.duration for s in scenes):.0f} 秒")
    os.makedirs(os.path.join(here, "build"), exist_ok=True)
    silent = os.path.join(here, "build", "video_only.mp4")
    wav = os.path.join(here, "build", "soundtrack.wav")
    soundtrack(scenes, voices, wav)
    render_film(scenes, silent)
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                    "-i", silent, "-i", wav, "-map", "0:v", "-map", "1:a",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", out], check=True)
    print("完成：", out)

"""音轨：旁白（本地 Kokoro 语音合成）+ 1950 年代风格轻爵士配乐 + 马克笔沙沙声。

全部在本地生成：
- 旁白用 sherpa-onnx 运行 Kokoro v1.1-zh 模型（Apache-2.0），首次运行自动下载。
- 配乐用 numpy 合成：FM 电钢琴和弦、行走贝斯、鼓刷，摇摆节奏。
- 沙沙声根据时间线上每一笔的起止时间和速度，由滤波噪声生成。
"""
import math
import os
import tarfile
import urllib.request
import wave

import numpy as np
from scipy.signal import butter, resample_poly, sosfilt

SR = 44100
HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
MODEL_DIR = os.path.join(BUILD, "kokoro-multi-lang-v1_1")
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "tts-models/kokoro-multi-lang-v1_1.tar.bz2")
VOICE_SID = 63              # 低沉的中文男声
VOICE_SPEED = 0.95
SENTENCE_GAP = 0.32         # 句与句之间的停顿（秒）

rng = np.random.default_rng(1956)


# ---------------------------------------------------------------- 工具

def write_wav(path, x, sr=SR):
    x = np.clip(x, -1, 1)
    pcm = (x * 32767).astype(np.int16)
    if pcm.ndim == 1:
        pcm = np.stack([pcm, pcm], axis=1)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def read_wav_mono(path):
    with wave.open(path, "rb") as w:
        n, ch, sr = w.getnframes(), w.getnchannels(), w.getframerate()
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768
    return x.reshape(-1, ch).mean(axis=1), sr


def bandpass(x, lo, hi, order=4):
    return sosfilt(butter(order, [lo, hi], btype="band", fs=SR, output="sos"), x)


def lowpass(x, f, order=2):
    return sosfilt(butter(order, f, btype="low", fs=SR, output="sos"), x)


def smooth_env(env, ms):
    """把控制信号平滑一下，避免咔哒声。"""
    return lowpass(env, 1000.0 / ms, order=1)


# ---------------------------------------------------------------- 旁白

_tts = None


def _ensure_model():
    if os.path.exists(os.path.join(MODEL_DIR, "model.onnx")):
        return
    os.makedirs(BUILD, exist_ok=True)
    archive = os.path.join(BUILD, "kokoro.tar.bz2")
    print("下载语音模型（约 350MB）……", flush=True)
    urllib.request.urlretrieve(MODEL_URL, archive)
    with tarfile.open(archive) as t:
        t.extractall(BUILD)
    os.remove(archive)


def _engine():
    global _tts
    if _tts is None:
        import sherpa_onnx
        _ensure_model()
        m = MODEL_DIR + "/"
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                    model=m + "model.onnx", voices=m + "voices.bin", tokens=m + "tokens.txt",
                    data_dir=m + "espeak-ng-data", dict_dir=m + "dict",
                    lexicon=m + "lexicon-us-en.txt," + m + "lexicon-zh.txt"),
                num_threads=os.cpu_count() or 4),
            rule_fsts=m + "date-zh.fst," + m + "phone-zh.fst," + m + "number-zh.fst")
        _tts = sherpa_onnx.OfflineTts(cfg)
    return _tts


def narrate(text, cache_name):
    """把一段旁白（按句号/问号切句）合成为 SR 采样率的单声道数组，结果缓存为 wav。"""
    os.makedirs(BUILD, exist_ok=True)
    path = os.path.join(BUILD, f"narration_{cache_name}.wav")
    stamp = path + ".txt"
    if os.path.exists(path) and os.path.exists(stamp) and open(stamp).read() == text:
        return read_wav_mono(path)[0]
    tts = _engine()
    pieces = []
    for sent in _split(text):
        a = tts.generate(sent, sid=VOICE_SID, speed=VOICE_SPEED)
        x = np.array(a.samples, dtype=np.float32)
        x = resample_poly(x, SR, a.sample_rate) if a.sample_rate != SR else x
        pieces += [_trim(x), np.zeros(int(SENTENCE_GAP * SR), np.float32)]
    x = np.concatenate(pieces[:-1])
    x = 0.89 * x / (np.abs(x).max() + 1e-9)
    write_wav(path, x)
    open(stamp, "w").write(text)
    return x


def _split(text):
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch in "。？！":
            out.append(cur)
            cur = ""
    if cur.strip():
        out.append(cur)
    return out


def _trim(x, thresh=0.01):
    idx = np.where(np.abs(x) > thresh * np.abs(x).max())[0]
    return x[max(0, idx[0] - 400): idx[-1] + 2000] if len(idx) else x


# ---------------------------------------------------------------- 马克笔沙沙声

def marker_track(scenes, offsets, total):
    """根据每个场景的时间线生成马克笔摩擦白板的声音。"""
    rate = 1000                                   # 控制信号采样率
    env = np.zeros(int(total * rate) + 1)
    taps = np.zeros_like(env)
    for tl, off in zip(scenes, offsets):
        for t0, t1, kind, data in tl.items:
            a, b = int((off + t0) * rate), int((off + t1) * rate)
            if b <= a:
                continue
            tt = np.arange(b - a) / rate
            if kind == "stroke":
                pts, width = data
                speed = _stroke_len(pts) / max(1e-3, t1 - t0)          # 像素/秒
                level = np.clip(0.35 + speed / 900, 0.35, 1.0) * (0.8 + width / 25)
                env[a:b] = np.maximum(env[a:b], level * (0.9 + 0.1 * np.sin(tt * 23)))
                taps[a] += 0.7
            else:
                # 写字：一下一下的短笔画
                burst = np.clip(np.sin(2 * np.pi * 6.5 * tt + rng.uniform(0, 6)), 0, 1) ** 0.6
                env[a:b] = np.maximum(env[a:b], 0.75 * burst)
    n = int(total * SR)
    env = np.interp(np.arange(n) / SR, np.arange(len(env)) / rate, env)
    env = smooth_env(env, 6)
    noise = rng.standard_normal(n)
    hiss = bandpass(noise, 1800, 7500) * 0.8 + bandpass(noise, 500, 1800) * 0.35
    # 白板表面不平整带来的颗粒感
    grain = 1 + 0.6 * lowpass(rng.standard_normal(n), 60) / 0.1
    hiss *= np.clip(grain, 0.2, 2.2)
    out = hiss * env
    # 落笔的轻微“嗒”声
    tap_sig = np.interp(np.arange(n) / SR, np.arange(len(taps)) / rate, taps)
    tap_sig = lowpass(tap_sig, 900) * 40
    out += tap_sig * bandpass(noise, 200, 2500)
    return 0.38 * out / (np.percentile(np.abs(out), 99.9) + 1e-9)


def _stroke_len(pts):
    p = np.asarray(pts)
    return float(np.hypot(*np.diff(p, axis=0).T).sum())


# ---------------------------------------------------------------- 配乐

NOTE = {"C": 0, "Db": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "Gb": 6, "G": 7, "Ab": 8,
        "A": 9, "Bb": 10, "B": 11}
QUAL = {"maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "7": [0, 4, 7, 10], "6": [0, 4, 7, 9]}
# 八小节循环：F 大调 I–vi–ii–V 的变奏
PROGRESSION = [("F", "maj7"), ("D", "m7"), ("G", "m7"), ("C", "7"),
               ("A", "m7"), ("D", "7"), ("G", "m7"), ("C", "7")]
BPM = 92


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def epiano(freq, dur, vel=0.5):
    """FM 电钢琴：调制指数随时间衰减。"""
    t = np.arange(int(dur * SR)) / SR
    idx = 1.6 * np.exp(-t * 5) + 0.25
    mod = np.sin(2 * np.pi * freq * t) * idx
    env = np.exp(-t * 2.2) * np.minimum(1, t / 0.005)
    rel = np.minimum(1, (dur - t) / 0.08)
    tone = np.sin(2 * np.pi * freq * t + mod) + 0.15 * np.sin(2 * np.pi * 2 * freq * t)
    return vel * tone * env * np.clip(rel, 0, 1)


def bass(freq, dur, vel=0.6):
    t = np.arange(int(dur * SR)) / SR
    env = np.exp(-t * 3.5) * np.minimum(1, t / 0.01) * np.clip((dur - t) / 0.05, 0, 1)
    tone = np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(4 * np.pi * freq * t) \
        + 0.12 * np.sin(6 * np.pi * freq * t)
    return vel * tone * env


def brush(dur, vel=0.3, swish=False):
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = rng.standard_normal(n)
    if swish:
        env = np.sin(np.pi * np.clip(t / dur, 0, 1)) ** 2
        return vel * bandpass(x, 1500, 6000, 2) * env
    env = np.exp(-t * 28)
    return vel * bandpass(x, 3000, 10000, 2) * env


def music_track(total):
    n = int(total * SR) + SR
    out = np.zeros(n)
    beat = 60.0 / BPM
    swing = beat * 2 / 3                       # 摇摆八分音符的后半拍位置

    def add(sig, t):
        i = int(t * SR)
        if i < n:
            out[i:i + len(sig)] += sig[:n - i]

    bar, t = 0, 0.0
    while t < total:
        root, q = PROGRESSION[bar % len(PROGRESSION)]
        nxt_root, _ = PROGRESSION[(bar + 1) % len(PROGRESSION)]
        r = 48 + NOTE[root]                                   # 低音区根音
        tones = QUAL[q]
        # 行走贝斯：根音、三音、五音、通往下一小节根音的经过音
        nxt = 48 + NOTE[nxt_root]
        approach = nxt + (1 if (bar % 2) else -1)
        for k, m in enumerate([r, r + tones[1], r + tones[2], approach]):
            m = m - 12 if m > 55 else m
            add(bass(midi_hz(m - 12), beat * 0.95, 0.55 if k == 0 else 0.42), t + k * beat)
        # 电钢琴和弦：第 2 拍和第 3 拍的后半拍
        voicing = [60 + NOTE[root] + iv for iv in tones]
        voicing = [v - 12 if v > 72 else v for v in voicing]
        for when, vel, d in ((beat, 0.11, beat * 0.9), (2 * beat + swing, 0.09, beat * 1.4)):
            for v in voicing:
                add(epiano(midi_hz(v), d, vel), t + when)
        # 鼓刷：每拍一个擦刷，2、4 拍加一个轻点
        for k in range(4):
            add(brush(beat * 0.9, 0.05, swish=True), t + k * beat)
            if k % 2:
                add(brush(0.15, 0.09), t + k * beat)
            add(brush(0.08, 0.035), t + k * beat + swing)
        t += 4 * beat
        bar += 1
    out = lowpass(out[:int(total * SR)], 7000)
    return out / (np.abs(out).max() + 1e-9)


# ---------------------------------------------------------------- 混音

def mix(narr, music, marker, total, fade_in=2.0, fade_out=4.0):
    n = int(total * SR)
    narr, music, marker = (np.pad(x, (0, max(0, n - len(x))))[:n] for x in (narr, music, marker))
    # 旁白出现时把配乐压低（侧链闪避）
    voice_env = smooth_env((np.abs(narr) > 0.02).astype(float), 250).clip(0, 1)
    duck = 1 - 0.55 * np.minimum(1, voice_env * 3)
    t = np.arange(n) / SR
    fade = np.minimum(1, t / fade_in) * np.clip((total - t) / fade_out, 0, 1)
    m = 0.20 * music * duck * fade
    x = 0.95 * narr + m + marker
    return 0.95 * np.tanh(x)            # 软限幅，峰值不超过 0.95

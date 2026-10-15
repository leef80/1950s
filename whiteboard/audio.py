"""音轨：旁白（本地 ZipVoice 语音合成）+ 1950 年代风格轻爵士配乐 + 马克笔沙沙声。

全部在本地生成：
- 旁白用 sherpa-onnx 运行 ZipVoice（零样本语音合成，模仿一段参考录音的音色和语气），
  首次运行自动下载模型。相比逐字拼读的模型，它的分句和连读自然得多。
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
RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
MODEL_DIR = os.path.join(BUILD, "sherpa-onnx-zipvoice-distill-int8-zh-en-emilia")
MODEL_URL = RELEASE + "tts-models/sherpa-onnx-zipvoice-distill-int8-zh-en-emilia.tar.bz2"
VOCODER = os.path.join(BUILD, "vocos_24khz.onnx")
VOCODER_URL = RELEASE + "vocoder-models/vocos_24khz.onnx"
# 参考男声：sherpa-onnx 完整版 ZipVoice 包里自带的示例录音（只取这一个文件）
PROMPT_WAV = os.path.join(BUILD, "zipvoice_prompt.wav")
PROMPT_ARCHIVE_URL = RELEASE + "tts-models/sherpa-onnx-zipvoice-distill-zh-en-emilia.tar.bz2"
PROMPT_TEXT = "周日被我射熄火了，所以今天是周一。"
# 参考录音语速偏慢。模型自带的 speed 参数大于 1 时短句会被截断，
# 所以按原速合成，再用 ffmpeg 的 atempo 变速不变调。
TEMPO = 1.35
FLOW_STEPS = 4
SENTENCE_GAP = 0.32         # 句与句之间的停顿（秒）

# 音量（线性幅度）。旁白峰值约 0.85、说话时平均约 -17 dB。
MARKER_RMS = 0.01           # 马克笔沙沙声的平均音量，约 -40 dB
MARKER_DUCK = 0.35          # 旁白说话时马克笔声再降到 35%（约 -9 dB）
MUSIC_LEVEL = 0.20
MUSIC_DUCK = 0.45           # 旁白说话时配乐降到 45%

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


def _download(url, path):
    print(f"下载 {os.path.basename(url)} ……", flush=True)
    os.makedirs(BUILD, exist_ok=True)
    urllib.request.urlretrieve(url, path + ".part")
    os.replace(path + ".part", path)


def _ensure_model():
    if not os.path.exists(os.path.join(MODEL_DIR, "lexicon.txt")):
        archive = MODEL_DIR + ".tar.bz2"
        _download(MODEL_URL, archive)
        with tarfile.open(archive) as t:
            t.extractall(BUILD)
        os.remove(archive)
    if not os.path.exists(VOCODER):
        _download(VOCODER_URL, VOCODER)
    if not os.path.exists(PROMPT_WAV):
        archive = PROMPT_WAV + ".tar.bz2"
        _download(PROMPT_ARCHIVE_URL, archive)          # 约 630MB，只为取出参考录音
        with tarfile.open(archive) as t:
            member = next(m for m in t.getmembers() if m.name.endswith("/prompt.wav"))
            with t.extractfile(member) as src, open(PROMPT_WAV, "wb") as dst:
                dst.write(src.read())
        os.remove(archive)


_prompt = None


def _engine():
    global _tts, _prompt
    if _tts is None:
        import sherpa_onnx
        _ensure_model()
        m = MODEL_DIR + "/"
        cfg = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(
            zipvoice=sherpa_onnx.OfflineTtsZipvoiceModelConfig(
                tokens=m + "tokens.txt", encoder=m + "encoder.int8.onnx",
                decoder=m + "decoder.int8.onnx", vocoder=VOCODER,
                data_dir=m + "espeak-ng-data", lexicon=m + "lexicon.txt"),
            num_threads=os.cpu_count() or 4))
        _tts = sherpa_onnx.OfflineTts(cfg)
        x, sr = read_wav_mono(PROMPT_WAV)
        _prompt = (x.tolist(), sr)
    return _tts


def narrate(text, cache_name):
    """把一段旁白（按句号/问号切句）合成为 SR 采样率的单声道数组，结果缓存为 wav。"""
    os.makedirs(BUILD, exist_ok=True)
    path = os.path.join(BUILD, f"narration_zipvoice_t{TEMPO}_{cache_name}.wav")
    stamp = path + ".txt"
    if os.path.exists(path) and os.path.exists(stamp) and open(stamp).read() == text:
        return read_wav_mono(path)[0]
    tts = _engine()
    pieces = []
    for sent in _split(text):
        a = tts.generate(sent, prompt_text=PROMPT_TEXT, prompt_samples=_prompt[0],
                         sample_rate=_prompt[1], speed=1.0, num_steps=FLOW_STEPS)
        x = _atempo(np.array(a.samples, dtype=np.float32), a.sample_rate, TEMPO)
        x = resample_poly(x, SR, a.sample_rate) if a.sample_rate != SR else x
        pieces += [_trim(x), np.zeros(int(SENTENCE_GAP * SR), np.float32)]
    x = np.concatenate(pieces[:-1])
    x = 0.89 * x / (np.abs(x).max() + 1e-9)
    write_wav(path, x)
    open(stamp, "w").write(text)
    return x


def _atempo(x, sr, k):
    """变速不变调（ffmpeg atempo，WSOLA）。"""
    import subprocess
    import imageio_ffmpeg
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error",
                        "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-",
                        "-filter:a", f"atempo={k}", "-f", "f32le", "-"],
                       input=x.astype(np.float32).tobytes(), capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32).copy()


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
    return x[max(0, idx[0] - 1600): idx[-1] + 2400] if len(idx) else x   # 句首留足余量，别剪掉辅音


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
    hiss *= np.clip(grain, 0.4, 1.6)
    out = hiss * env
    # 按有声部分的平均响度归一，避免个别峰值决定整体音量
    active = env > 0.05
    out *= MARKER_RMS / (np.sqrt((out[active] ** 2).mean()) + 1e-12)
    # 落笔的轻微“嗒”声：很轻，只做点缀
    tap_sig = np.interp(np.arange(n) / SR, np.arange(len(taps)) / rate, taps)
    tap_sig = lowpass(tap_sig, 900)
    tap = tap_sig * bandpass(noise, 200, 2500)
    out += MARKER_RMS * 0.8 * tap / (np.abs(tap).max() + 1e-12)
    return out


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
    speaking = np.minimum(1, voice_env * 3)
    t = np.arange(n) / SR
    fade = np.minimum(1, t / fade_in) * np.clip((total - t) / fade_out, 0, 1)
    m = MUSIC_LEVEL * music * (1 - (1 - MUSIC_DUCK) * speaking) * fade
    k = marker * (1 - (1 - MARKER_DUCK) * speaking)
    x = 0.95 * narr + m + k
    return 0.95 * np.tanh(x)            # 软限幅，峰值不超过 0.95

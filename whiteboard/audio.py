"""音轨：旁白（本地语音合成）+ 1950 年代风格轻爵士配乐 + 马克笔沙沙声。

全部在本地生成：
- 旁白用 sherpa-onnx 运行，首次运行自动下载模型。两种声音可选（script.toml 的 voice）：
  clear    （默认）先用 MeloTTS 念一句话作为参考录音，再让 ZipVoice 照着它的音色和语气念旁白。
           MeloTTS 音色干净、节奏自然，ZipVoice 发音准确，两者结合最清楚。
  melo     直接用 MeloTTS 中文女声（个别字发音不如 clear 准）。
  zipvoice ZipVoice 模仿你提供的参考录音。录音要清晰、全频带（16kHz 以上采样），
           否则合成结果会跟着沙哑或发闷。
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
MELO_DIR = os.path.join(BUILD, "vits-melo-tts-zh_en")
MELO_URL = RELEASE + "tts-models/vits-melo-tts-zh_en.tar.bz2"
MELO_SPEED = 0.92           # 稍慢一点，更像纪录片解说
ZIP_DIR = os.path.join(BUILD, "sherpa-onnx-zipvoice-distill-int8-zh-en-emilia")
ZIP_URL = RELEASE + "tts-models/sherpa-onnx-zipvoice-distill-int8-zh-en-emilia.tar.bz2"
VOCODER = os.path.join(BUILD, "vocos_24khz.onnx")
VOCODER_URL = RELEASE + "vocoder-models/vocos_24khz.onnx"
# ZipVoice 的 speed 参数大于 1 时短句会被截断，所以按原速合成，再用 atempo 变速不变调。
ZIP_TEMPO = 1.0
FLOW_STEPS = 4
SENTENCE_GAP = 0.32         # 句与句之间的停顿（秒）

# 音量（线性幅度）。旁白峰值约 0.85、说话时平均约 -17 dB。
MARKER_RMS = 0.01           # 马克笔沙沙声的平均音量，约 -40 dB
MARKER_DUCK = 0.35          # 旁白说话时马克笔声再降到 35%（约 -9 dB）
MUSIC_LEVEL = 0.20
MUSIC_DUCK = 0.35           # 旁白说话时配乐降到 35%

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

def _download(url, path):
    print(f"下载 {os.path.basename(url)} ……", flush=True)
    os.makedirs(BUILD, exist_ok=True)
    urllib.request.urlretrieve(url, path + ".part")
    os.replace(path + ".part", path)


def _ensure(dirpath, url, probe):
    if not os.path.exists(os.path.join(dirpath, probe)):
        archive = dirpath + ".tar.bz2"
        _download(url, archive)
        with tarfile.open(archive) as t:
            t.extractall(BUILD)
        os.remove(archive)


class MeloVoice:
    key = "melo"

    def __init__(self):
        import sherpa_onnx
        _ensure(MELO_DIR, MELO_URL, "model.onnx")
        m = MELO_DIR + "/"
        self.tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                    model=m + "model.onnx", lexicon=m + "lexicon.txt",
                    tokens=m + "tokens.txt", dict_dir=m + "dict"),
                num_threads=os.cpu_count() or 4),
            rule_fsts=m + "date.fst," + m + "phone.fst," + m + "number.fst"))

    def say(self, sent):
        sent = sent.replace("：", "，").replace("；", "，")     # 词典里没有这两个标点
        a = self.tts.generate(sent, sid=0, speed=MELO_SPEED)
        return np.array(a.samples, dtype=np.float32), a.sample_rate


class ZipVoice:
    def __init__(self, prompt_wav, prompt_text):
        import sherpa_onnx
        _ensure(ZIP_DIR, ZIP_URL, "lexicon.txt")
        if not os.path.exists(VOCODER):
            _download(VOCODER_URL, VOCODER)
        m = ZIP_DIR + "/"
        self.tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                zipvoice=sherpa_onnx.OfflineTtsZipvoiceModelConfig(
                    tokens=m + "tokens.txt", encoder=m + "encoder.int8.onnx",
                    decoder=m + "decoder.int8.onnx", vocoder=VOCODER,
                    data_dir=m + "espeak-ng-data", lexicon=m + "lexicon.txt"),
                num_threads=os.cpu_count() or 4)))
        x, sr = read_wav_mono(prompt_wav)
        self.prompt = (x.tolist(), sr, prompt_text)
        self.key = f"zipvoice_{os.path.splitext(os.path.basename(prompt_wav))[0]}_t{ZIP_TEMPO}"

    def say(self, sent):
        px, psr, ptxt = self.prompt
        a = self.tts.generate(sent, prompt_text=ptxt, prompt_samples=px, sample_rate=psr,
                              speed=1.0, num_steps=FLOW_STEPS)
        x = np.array(a.samples, dtype=np.float32)
        if ZIP_TEMPO != 1.0:
            x = _atempo(x, a.sample_rate, ZIP_TEMPO)
        return x, a.sample_rate


CLEAR_PROMPT_TEXT = "那是一个安静的早晨，街道两旁的房子整整齐齐，人们像往常一样出门上班。"


def clear_voice():
    """用 MeloTTS 生成干净的参考录音，再交给 ZipVoice。"""
    path = os.path.join(BUILD, "melo_prompt.wav")
    if not os.path.exists(path):
        x, sr = MeloVoice().say(CLEAR_PROMPT_TEXT)
        x = _trim(x)
        write_wav(path, 0.9 * x / np.abs(x).max(), sr)
    return ZipVoice(path, CLEAR_PROMPT_TEXT)


_voices = {}


def get_voice(settings):
    """settings 来自 script.toml 的 [settings]。"""
    kind = settings.get("voice", "clear")
    if kind not in _voices:
        if kind == "clear":
            _voices[kind] = clear_voice()
        elif kind == "melo":
            _voices[kind] = MeloVoice()
        elif kind == "zipvoice":
            _voices[kind] = ZipVoice(os.path.join(HERE, settings["prompt_wav"]),
                                     settings["prompt_text"])
        else:
            raise ValueError(f"未知的 voice：{kind}（可选 clear / melo / zipvoice）")
    return _voices[kind]


def narrate(text, cache_name, voice):
    """把一段旁白（按句号/问号切句）合成为 SR 采样率的单声道数组，结果缓存为 wav。"""
    os.makedirs(BUILD, exist_ok=True)
    path = os.path.join(BUILD, f"narration_{voice.key}_{cache_name}.wav")
    stamp = path + ".txt"
    if os.path.exists(path) and os.path.exists(stamp) and open(stamp).read() == text:
        return read_wav_mono(path)[0]
    pieces = []
    for sent in _split(text):
        x, sr = voice.say(sent)
        x = resample_poly(x, SR, sr) if sr != SR else x
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

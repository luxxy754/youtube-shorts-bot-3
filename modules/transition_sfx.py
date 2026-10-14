"""
Transition sound effects - pure numpy synthesis, no external files.

Every transition type has a matching sound:
  - FADE / DISSOLVE       -> soft whoosh
  - WIPE / SLIDE          -> sharp whoosh with pitch sweep
  - PAPER SHUFFLE         -> paper rustle (filtered noise bursts)
  - GLITCH (RGB split)    -> digital static + bitcrush beep
  - SCANLINE / PIXELSORT  -> CRT buzz + interference
  - LIGHT LEAK            -> warm shimmer + swell
  - FILM BURN             -> fire crackle + low boom
  - FLASH (white)         -> bright "tick" + reverse reverb tail
  - WHIP ZOOM             -> doppler whoosh (up-down pitch)
  - SPIN                  -> rotating swoosh (panning noise)
  - SHAKE                 -> impact thud + camera rattle
  - ZOOM BLUR             -> soft riser
  - INK DRIP              -> liquid drop + low gurgle
  - PUNCH ZOOM            -> hard kick drum

All sounds are normalized to a target peak. Gains are set by style so no
two videos sound identical.
"""

import numpy as np

SR = 44100


# ---------------------------------------------------------------- utils ----
def _t(dur):
    return np.arange(int(SR * dur), dtype=np.float64) / SR


def _norm(x, peak=0.9):
    m = float(np.max(np.abs(x))) or 1.0
    return (x / m * peak).astype(np.float32)


def _fade(x, in_d=0.005, out_d=0.02):
    n = len(x)
    if n < 4:
        return x
    fi = int(SR * in_d)
    fo = int(SR * out_d)
    if fi > 0 and fi < n:
        x[:fi] *= np.linspace(0, 1, fi)
    if fo > 0 and fo < n:
        x[-fo:] *= np.linspace(1, 0, fo)
    return x


def _svf_bandpass(noise, f_center, q=2.0):
    """Static bandpass around f_center."""
    f = 2.0 * np.sin(np.pi * f_center / SR)
    damp = 1.0 / q
    low = band = 0.0
    out = np.empty(len(noise), dtype=np.float64)
    for i in range(len(noise)):
        low += f * band
        high = noise[i] - low - damp * band
        band += f * high
        out[i] = band
    return out


def _svf_sweep(noise, f_start, f_end, q=2.0):
    """Band-pass that sweeps from f_start to f_end."""
    n = len(noise)
    freqs = np.geomspace(max(f_start, 20), min(f_end, SR // 2 - 100), n)
    f = 2.0 * np.sin(np.pi * freqs / SR)
    damp = 1.0 / q
    low = band = 0.0
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        low += f[i] * band
        high = noise[i] - low - damp * band
        band += f[i] * high
        out[i] = band
    return out


# ================================================================
# TRANSITION SFX
# ================================================================

def sfx_fade(rng=None, dur=0.30):
    rng = rng or np.random.default_rng()
    n = int(SR * dur)
    noise = rng.standard_normal(n)
    sig = _svf_sweep(noise, 400, 2400, q=1.4)
    env = np.sin(np.pi * np.linspace(0, 1, n)) ** 1.6
    return _fade(_norm(sig * env, 0.5), 0.01, 0.05)


def sfx_wipe(rng=None, dur=0.26, direction="left"):
    rng = rng or np.random.default_rng()
    n = int(SR * dur)
    noise = rng.standard_normal(n)
    if direction in ("left", "up"):
        sig = _svf_sweep(noise, 2400, 400, q=1.6)
    else:
        sig = _svf_sweep(noise, 400, 2400, q=1.6)
    t = np.linspace(0, 1, n)
    env = np.sin(np.pi * t) ** 1.2
    return _fade(_norm(sig * env, 0.55), 0.008, 0.04)


def sfx_slide(rng=None, dur=0.30):
    rng = rng or np.random.default_rng()
    n = int(SR * dur)
    noise = rng.standard_normal(n)
    sig = _svf_sweep(noise, 300, 3800, q=1.0)
    t = np.linspace(0, 1, n)
    env = np.exp(-((t - 0.5) ** 2) / 0.06)
    return _fade(_norm(sig * env, 0.5), 0.01, 0.06)


def sfx_paper_shuffle(rng=None, dur=0.35):
    """Paper rustle - filtered noise bursts."""
    rng = rng or np.random.default_rng()
    n = int(SR * dur)
    track = np.zeros(n, dtype=np.float64)

    n_flips = int(rng.integers(4, 7))
    for k in range(n_flips):
        t0 = int(rng.uniform(0, dur * 0.85) * SR)
        len_flip = int(rng.uniform(0.02, 0.05) * SR)
        if t0 + len_flip > n:
            continue
        burst = rng.standard_normal(len_flip)
        f_center = rng.uniform(3500, 6500)
        burst = _svf_bandpass(burst, f_center, q=1.2)
        env = np.exp(-np.linspace(0, 8, len_flip))
        track[t0:t0 + len_flip] += burst * env * rng.uniform(0.5, 1.0)

    low = _t(dur)
    low_sig = np.sin(2 * np.pi * 80 * low) * np.exp(-low * 15)
    track += low_sig * 0.3

    return _fade(_norm(track, 0.55), 0.005, 0.08)


def sfx_glitch(rng=None, dur=0.24):
    rng = rng or np.random.default_rng()
    n = int(SR * dur)
    noise = rng.standard_normal(n)
    hold = 40
    crushed = np.repeat(noise[::hold], hold)[:n]
    if len(crushed) < n:
        crushed = np.pad(crushed, (0, n - len(crushed)))
    t = _t(dur)
    freq = 800 + 400 * np.sin(2 * np.pi * 30 * t)
    square = np.sign(np.sin(2 * np.pi * freq * t))
    gate = (rng.random(n) < 0.7).astype(np.float64)
    sig = (crushed * 0.4 + square * 0.6) * gate
    env = np.exp(-t * 6) * (1 - np.exp(-t * 50))
    return _fade(_norm(sig * env, 0.6), 0.002, 0.03)


def sfx_scanline(rng=None, dur=0.30):
    rng = rng or np.random.default_rng()
    t = _t(dur)
    n = len(t)
    hum = np.sin(2 * np.pi * 60 * t) * 0.4
    hum += np.sin(2 * np.pi * 120 * t) * 0.2
    buzz = rng.standard_normal(n) * 0.5
    buzz = _svf_bandpass(buzz, 4000, q=0.8)
    sig = hum + buzz
    env = np.sin(np.pi * t / dur) ** 1.5
    return _fade(_norm(sig * env, 0.5), 0.01, 0.05)


def sfx_lightleak(rng=None, dur=0.40):
    rng = rng or np.random.default_rng()
    t = _t(dur)
    n = len(t)
    tone1 = np.sin(2 * np.pi * 220 * t) * 0.3
    tone2 = np.sin(2 * np.pi * 330 * t) * 0.2
    tone3 = np.sin(2 * np.pi * 440 * t) * 0.15
    shimmer = rng.standard_normal(n) * 0.15
    shimmer = _svf_bandpass(shimmer, 6000, q=1.5)
    env = np.sin(np.pi * t / dur) ** 0.9
    sig = (tone1 + tone2 + tone3 + shimmer) * env
    return _fade(_norm(sig, 0.45), 0.02, 0.10)


def sfx_filmburn(rng=None, dur=0.45):
    rng = rng or np.random.default_rng()
    t = _t(dur)
    n = len(t)
    crackle = np.zeros(n)
    for _ in range(int(dur * 30)):
        p = int(rng.uniform(0, n - 100))
        ln = int(rng.uniform(20, 60))
        crackle[p:p + ln] += rng.standard_normal(ln) * np.exp(-np.linspace(0, 5, ln))
    boom_freq = 55 + 30 * np.exp(-t * 6)
    boom = np.sin(2 * np.pi * np.cumsum(boom_freq) / SR) * np.exp(-t * 3)
    sig = crackle * 0.4 + boom * 0.7
    env = np.exp(-t * 2.5) * (1 - np.exp(-t * 30))
    return _fade(_norm(sig * env, 0.6), 0.005, 0.08)


def sfx_flash(rng=None, dur=0.28):
    rng = rng or np.random.default_rng()
    t = _t(dur)
    n = len(t)
    tick = np.sin(2 * np.pi * 3000 * t) * np.exp(-t * 80)
    tick += rng.standard_normal(n) * np.exp(-t * 120) * 0.5
    tail = rng.standard_normal(n) * np.exp(-(dur - t) * 4)
    tail = _svf_bandpass(tail, 2500, q=1.0)
    sig = tick * 0.7 + tail * 0.2
    return _fade(_norm(sig, 0.6), 0.002, 0.06)


def sfx_whipzoom(rng=None, dur=0.32):
    rng = rng or np.random.default_rng()
    n = int(SR * dur)
    noise = rng.standard_normal(n)
    half = n // 2
    up = _svf_sweep(noise[:half], 400, 4000, q=1.2)
    down = _svf_sweep(noise[half:], 4000, 400, q=1.2)
    sig = np.concatenate([up, down])
    if len(sig) < n:
        sig = np.pad(sig, (0, n - len(sig)))
    env = np.sin(np.pi * np.linspace(0, 1, n)) ** 0.7
    return _fade(_norm(sig * env, 0.6), 0.005, 0.05)


def sfx_spin(rng=None, dur=0.35):
    rng = rng or np.random.default_rng()
    n = int(SR * dur)
    noise = rng.standard_normal(n)
    sig = _svf_bandpass(noise, 1500, q=1.0)
    t = np.linspace(0, 1, n)
    tremolo = 0.5 + 0.5 * np.sin(2 * np.pi * 8 * t)
    env = np.sin(np.pi * t) ** 1.2
    return _fade(_norm(sig * tremolo * env, 0.5), 0.01, 0.06)


def sfx_shake(rng=None, dur=0.28):
    rng = rng or np.random.default_rng()
    t = _t(dur)
    n = len(t)
    thud_freq = 80 + 40 * np.exp(-t * 10)
    thud = np.sin(2 * np.pi * np.cumsum(thud_freq) / SR) * np.exp(-t * 8)
    rattle = np.zeros(n)
    for k in range(6):
        pos = int(rng.uniform(0, n - 200))
        ln = int(rng.uniform(50, 150))
        burst = rng.standard_normal(ln) * np.exp(-np.linspace(0, 6, ln))
        rattle[pos:pos + ln] += burst
    rattle = _svf_bandpass(rattle, 3000, q=1.5)
    sig = thud * 0.7 + rattle * 0.4
    return _fade(_norm(sig, 0.6), 0.003, 0.05)


def sfx_zoomblur(rng=None, dur=0.35):
    rng = rng or np.random.default_rng()
    t = _t(dur)
    n = len(t)
    freq = np.geomspace(180, 900, n)
    tone = np.sin(2 * np.pi * np.cumsum(freq) / SR) * 0.5
    noise = rng.standard_normal(n)
    noise = _svf_sweep(noise, 300, 5000, q=1.0)
    env = (t / dur) ** 2.5
    sig = (tone + noise * 0.6) * env
    return _fade(_norm(sig, 0.55), 0.01, 0.06)


def sfx_inkdrip(rng=None, dur=0.38):
    rng = rng or np.random.default_rng()
    t = _t(dur)
    n = len(t)
    drop_freq = 800 * np.exp(-t * 25) + 150
    drop = np.sin(2 * np.pi * np.cumsum(drop_freq) / SR) * np.exp(-t * 12)
    gurgle = np.sin(2 * np.pi * 90 * t + 3 * np.sin(2 * np.pi * 6 * t))
    gurgle *= np.exp(-t * 4)
    sub = np.sin(2 * np.pi * 45 * t) * np.exp(-t * 3)
    sig = drop * 0.6 + gurgle * 0.3 + sub * 0.3
    return _fade(_norm(sig, 0.55), 0.003, 0.08)


def sfx_punchzoom(rng=None, dur=0.22):
    rng = rng or np.random.default_rng()
    t = _t(dur)
    n = len(t)
    freq = 40 + 100 * np.exp(-t * 30)
    body = np.sin(2 * np.pi * np.cumsum(freq) / SR) * np.exp(-t * 10)
    click = rng.standard_normal(n) * np.exp(-t * 150) * 0.4
    sig = body + click
    return _fade(_norm(np.tanh(sig * 1.5), 0.7), 0.002, 0.04)


# ================================================================
# DISPATCHER
# ================================================================
def _mode_kind(mode):
    """Return the SFX family for a transition mode."""
    if mode is None:
        return None
    if mode in ("fade", "dissolve", "fadeblack", "fadewhite", "distance"):
        return "fade"
    if mode.startswith("wipe"):
        return "wipe"
    if mode.startswith("slide"):
        return "slide"
    if mode in ("papershuffle", "hlslice", "hrslice", "vuslice", "vdslice"):
        return "paper"
    if mode in ("rgbsplit", "datamosh", "pixelsort"):
        return "glitch"
    if mode == "scanline":
        return "scanline"
    if mode == "lightleak":
        return "light"
    if mode == "filmburn":
        return "filmburn"
    if mode in ("flash_white", "flash_color"):
        return "flash"
    if mode.startswith("whipzoom"):
        return "whipzoom"
    if mode in ("spin", "spinzoom"):
        return "spin"
    if mode == "shake":
        return "shake"
    if mode == "zoomblur":
        return "zoomblur"
    if mode == "inkdrip":
        return "inkdrip"
    if mode == "punchzoom":
        return "punch"
    if mode in ("circleopen", "circleclose", "radial",
                "horzopen", "horzclose", "vertopen", "vertclose",
                "diagtl", "diagtr", "diagbl", "diagbr",
                "smoothleft", "smoothright", "smoothup", "smoothdown"):
        return "wipe"
    if mode == "pixelize":
        return "glitch"
    return "fade"


SFX_BUILDERS = {
    "fade": sfx_fade,
    "wipe": sfx_wipe,
    "slide": sfx_slide,
    "paper": sfx_paper_shuffle,
    "glitch": sfx_glitch,
    "scanline": sfx_scanline,
    "light": sfx_lightleak,
    "filmburn": sfx_filmburn,
    "flash": sfx_flash,
    "whipzoom": sfx_whipzoom,
    "spin": sfx_spin,
    "shake": sfx_shake,
    "zoomblur": sfx_zoomblur,
    "inkdrip": sfx_inkdrip,
    "punch": sfx_punchzoom,
}


def build_transition_sfx(mode, rng=None, dur=None):
    """
    Return (sfx_audio_array, duration). mode=None returns silence.
    """
    kind = _mode_kind(mode)
    if kind is None:
        return None, 0.0
    builder = SFX_BUILDERS.get(kind, sfx_fade)
    rng = rng or np.random.default_rng()
    if dur is None:
        base = {
            "fade": 0.30, "wipe": 0.26, "slide": 0.30, "paper": 0.35,
            "glitch": 0.24, "scanline": 0.30, "light": 0.40, "filmburn": 0.45,
            "flash": 0.28, "whipzoom": 0.32, "spin": 0.35, "shake": 0.28,
            "zoomblur": 0.35, "inkdrip": 0.38, "punch": 0.22,
        }.get(kind, 0.30)
        dur = base * rng.uniform(0.85, 1.15)
    return builder(rng=rng, dur=dur), dur

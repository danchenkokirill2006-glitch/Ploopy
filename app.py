from __future__ import annotations

import os
import io
import json
import ctypes
import ctypes.wintypes
import hashlib
import math
import queue
import re
import subprocess
import sys
import tempfile
import traceback
import threading
import time
import winsound
import urllib.parse
import urllib.request
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk, font as tkfont, simpledialog, messagebox
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageTk
import pystray
from version import APP_VERSION as DEFAULT_APP_VERSION, GITHUB_REPOSITORY

DEFAULT_DIR = Path.home() / "Downloads" / "Загрузки по ссылке"
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
try:
    APP_VERSION = (APP_DIR / "version.txt").read_text(encoding="utf-8").strip()
except OSError:
    APP_VERSION = DEFAULT_APP_VERSION
SETTINGS_PATH = APP_DIR / "settings.json"
HISTORY_PATH = APP_DIR / "history.json"
UI_CONFIG_PATH = APP_DIR / "ui_customization.json"
ELEMENT_EDITS_PATH = APP_DIR / "ui_element_edits.json"
HERO_ART_PATH = APP_DIR / "Ploopy-hero.png"
DOWNLOAD_ANIMATION_PATH = APP_DIR / "Ploopy-download-smooth.gif"
FONT_DIR = APP_DIR / "fonts"
SVG_ICON_DIR = APP_DIR / "assets" / "lucide" / "icons"
VENDOR_DIR = APP_DIR / "vendor"
if VENDOR_DIR.is_dir() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))
try:
    import resvg_py
except ImportError:
    resvg_py = None

APP_FONT = "Manrope"
AA_RENDER_SCALE = 4
SHAPE_CACHE_LIMIT = 16

DEFAULT_UI_CONFIG = {
    "brand": "Ploopy",
    "heading_one": "Ploopy готов",
    "heading_two": "грузить!",
    "description": "Вставь ссылку, и мы сохраним\nфайл на твой компьютер",
    "placeholder": "Вставь ссылку...",
    "download_button": "↓  Скачать",
    "hero_image": str(HERO_ART_PATH),
    "hero_scale": 130,
    "hero_x": 0,
    "hero_y": 0,
    "copy_x": 52,
    "copy_y": 64,
    "heading_size": 60,
    "description_size": 15,
    "background": "#eef5ff",
    "panel": "#f8fbff",
    "heading_color": "#131b2e",
    "body_color": "#606f89",
}


def load_ui_customization():
    config = DEFAULT_UI_CONFIG.copy()
    try:
        saved = json.loads(UI_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            config.update({key: value for key, value in saved.items() if key in config})
    except (OSError, ValueError, TypeError):
        pass
    return config


UI_CUSTOMIZATION = load_ui_customization()
APP_SURFACE = UI_CUSTOMIZATION["background"]
PANEL_SURFACE = UI_CUSTOMIZATION["panel"]
SVG_ICON_CACHE = {}
SHAPE_CACHE = {}


def _parse_hex(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha


def render_aa_pill(
    width: int,
    height: int,
    radius: int,
    fill: str,
    outline: str | None = None,
    outline_width: int = 1,
    shadow_color: str | None = None,
    shadow_blur: int = 0,
    shadow_offset: tuple[int, int] = (0, 0),
    pad: int = 0,
) -> Image.Image:
    """High-DPI 4x supersampled rounded rectangle with optional soft drop shadow."""
    scale = AA_RENDER_SCALE
    key = (width, height, radius, fill, outline, outline_width, shadow_color, shadow_blur, shadow_offset, pad, scale)
    cached = SHAPE_CACHE.get(key)
    if cached is not None:
        return cached

    total_w = max(4, width) * scale
    total_h = max(4, height) * scale
    p = pad * scale
    r = min(radius * scale, (total_w - 2 * p) // 2, (total_h - 2 * p) // 2)
    box = (p, p, total_w - p - 1, total_h - p - 1)

    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    if shadow_color and shadow_blur > 0:
        sx, sy = shadow_offset[0] * scale, shadow_offset[1] * scale
        s_layer = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
        s_draw = ImageDraw.Draw(s_layer)
        s_box = (box[0] + sx, box[1] + sy, box[2] + sx, box[3] + sy)
        s_draw.rounded_rectangle(s_box, radius=r, fill=_parse_hex(shadow_color, 42))
        s_layer = s_layer.filter(ImageFilter.GaussianBlur(shadow_blur * scale // 2))
        canvas.alpha_composite(s_layer)

    draw = ImageDraw.Draw(canvas)
    fill_rgba = _parse_hex(fill, 255) if fill else (0, 0, 0, 0)
    outline_rgba = _parse_hex(outline, 255) if outline else None
    draw.rounded_rectangle(
        box,
        radius=r,
        fill=fill_rgba,
        outline=outline_rgba,
        width=outline_width * scale if outline_rgba else 0,
    )
    result = canvas.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)
    SHAPE_CACHE[key] = result
    if len(SHAPE_CACHE) > SHAPE_CACHE_LIMIT:
        SHAPE_CACHE.pop(next(iter(SHAPE_CACHE)))
    return result


def render_aa_gradient_pill(
    width: int,
    height: int,
    radius: int,
    top: str,
    bottom: str,
    outline: str | None = None,
    shadow: bool = False,
    shadow_pad: int = 6,
) -> Image.Image:
    """Render a soft vertical gradient capsule with antialiased edges and unclipped shadow."""
    cache_key = ("gradient_pill", width, height, radius, top, bottom, outline, shadow, shadow_pad)
    cached = SHAPE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    scale = AA_RENDER_SCALE
    pad = shadow_pad if shadow else 0
    w, h = max(4, width) * scale, max(4, height) * scale
    p = pad * scale
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if shadow:
        alpha = Image.new("L", (w, h), 0)
        ImageDraw.Draw(alpha).rounded_rectangle(
            (p, p, w-p-1, h-p-1), radius=min(radius * scale, (h - 2*p) // 2), fill=48
        )
        alpha = alpha.filter(ImageFilter.GaussianBlur(10 * scale // 2))
        shadow_image = Image.new("RGBA", (w, h), (88, 133, 201, 0))
        shadow_image.putalpha(alpha)
        image.alpha_composite(shadow_image)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (p, p, w-p-1, h-p-1), radius=min(radius * scale, (h - 2*p) // 2), fill=255
    )
    top_rgb, bottom_rgb = _parse_hex(top)[:3], _parse_hex(bottom)[:3]
    strip = Image.new("RGBA", (1, max(1, h - 2*p)), (0, 0, 0, 0))
    strip_draw = ImageDraw.Draw(strip)
    for y in range(p, h-p):
        t = (y-p) / max(1, h - 2*p - 1)
        color = tuple(round(a * (1-t) + b*t) for a, b in zip(top_rgb, bottom_rgb))
        strip_draw.point((0, y-p), fill=(*color, 255))
    gradient = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gradient.paste(strip.resize((w - 2*p, h - 2*p), Image.Resampling.BILINEAR), (p, p))
    gradient.putalpha(mask)
    image.alpha_composite(gradient)
    if outline:
        ImageDraw.Draw(image).rounded_rectangle(
            (p, p, w-p-1, h-p-1), radius=min(radius*scale, (h-2*p)//2),
            outline=_parse_hex(outline), width=scale
        )
    result = image.resize((max(1, width), max(1, height)), Image.Resampling.LANCZOS)
    SHAPE_CACHE[cache_key] = result
    if len(SHAPE_CACHE) > SHAPE_CACHE_LIMIT:
        SHAPE_CACHE.pop(next(iter(SHAPE_CACHE)))
    return result


def trim_transparent_art(image: Image.Image, threshold: int = 12) -> Image.Image:
    """Trim only transparent padding so the full illustration scales up cleanly."""
    rgba = image.convert("RGBA")
    bounds = rgba.getchannel("A").point(lambda value: 255 if value > threshold else 0).getbbox()
    return rgba.crop(bounds) if bounds else rgba


def make_file_badge_photo(master, kind: str) -> ImageTk.PhotoImage:
    """Render the custom blue file type badge with a subtle download arrow inside, matching the concept."""
    key = ("file_badge", kind)
    if key in SVG_ICON_CACHE:
        return SVG_ICON_CACHE[key]

    scale = 4
    size = 52 * scale
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Outer light blue rounded square
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=15 * scale, fill=_parse_hex("#e5effe"))

    blue = _parse_hex("#2b7ff6")
    white = (255, 255, 255, 255)

    if kind == "video":
        # Camera body + lens triangle on the right
        bx1, by1, bx2, by2 = 12 * scale, 16 * scale, 31 * scale, 36 * scale
        d.rounded_rectangle((bx1, by1, bx2, by2), radius=5 * scale, fill=blue)
        poly = [
            (30 * scale, 22 * scale),
            (39 * scale, 17 * scale),
            (39 * scale, 35 * scale),
            (30 * scale, 30 * scale),
        ]
        d.polygon(poly, fill=blue)
        # Play triangle inside camera body
        play = [
            (19 * scale, 22 * scale),
            (19 * scale, 30 * scale),
            (26 * scale, 26 * scale),
        ]
        d.polygon(play, fill=white)
    elif kind == "image":
        # Rounded picture frame with mountain and sun
        bx1, by1, bx2, by2 = 13 * scale, 14 * scale, 39 * scale, 38 * scale
        d.rounded_rectangle((bx1, by1, bx2, by2), radius=5 * scale, fill=blue)
        d.rounded_rectangle(
            (bx1 + 2.5 * scale, by1 + 2.5 * scale, bx2 - 2.5 * scale, by2 - 2.5 * scale),
            radius=3 * scale,
            fill=_parse_hex("#e5effe"),
        )
        # Sun
        d.ellipse((18 * scale, 19 * scale, 23 * scale, 24 * scale), fill=blue)
        # Mountains
        mtn = [
            (15 * scale, 35 * scale),
            (23 * scale, 26 * scale),
            (29 * scale, 32 * scale),
            (33 * scale, 28 * scale),
            (37 * scale, 35 * scale),
        ]
        d.polygon(mtn, fill=blue)
    else:
        # Archive box with downward arrow inside
        bx1, by1, bx2, by2 = 13 * scale, 14 * scale, 39 * scale, 38 * scale
        d.rounded_rectangle((bx1, by1, bx2, by2), radius=5 * scale, fill=blue)
        # Lid separator line
        d.line((bx1, 21 * scale, bx2, 21 * scale), fill=_parse_hex("#e5effe"), width=2 * scale)
        # Vertical zip/arrow line
        cx = 26 * scale
        d.line((cx, 16 * scale, cx, 31 * scale), fill=white, width=int(2.2 * scale))
        d.polygon(
            [(cx - 4 * scale, 28 * scale), (cx + 4 * scale, 28 * scale), (cx, 33 * scale)],
            fill=white,
        )

    out = img.resize((52, 52), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(out, master=master)
    SVG_ICON_CACHE[key] = photo
    return photo


def svg_icon_photo(master, name, size, color="#526784"):
    """Render an official Lucide SVG at 4x and downsample cleanly for Tk."""
    key = (name, int(size), color)
    cached = SVG_ICON_CACHE.get(key)
    if cached is not None:
        return cached
    svg_path = SVG_ICON_DIR / f"{name}.svg"
    if resvg_py is not None and svg_path.is_file():
        svg = svg_path.read_text(encoding="utf-8").replace("currentColor", color)
        scale = AA_RENDER_SCALE
        png = resvg_py.svg_to_bytes(
            svg_string=svg, width=max(1, size * scale), height=max(1, size * scale)
        )
        image = Image.open(io.BytesIO(png)).convert("RGBA")
        image = image.resize((size, size), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image, master=master)
        SVG_ICON_CACHE[key] = photo
        return photo

    # Clean PIL fallback if resvg_py or svg file is absent
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    col = _parse_hex(color)
    w = max(2, int(size * 0.1 * scale))
    if name == "download":
        d.line((s // 2, int(s * 0.18), s // 2, int(s * 0.68)), fill=col, width=w)
        d.line((int(s * 0.3), int(s * 0.48), s // 2, int(s * 0.68)), fill=col, width=w)
        d.line((int(s * 0.7), int(s * 0.48), s // 2, int(s * 0.68)), fill=col, width=w)
        d.line((int(s * 0.22), int(s * 0.82), int(s * 0.78), int(s * 0.82)), fill=col, width=w)
    elif name == "minus":
        d.line((int(s * 0.22), s // 2, int(s * 0.78), s // 2), fill=col, width=w)
    elif name == "square":
        d.rounded_rectangle((int(s * 0.22), int(s * 0.22), int(s * 0.78), int(s * 0.78)), radius=int(s * 0.1), outline=col, width=w)
    elif name == "x":
        d.line((int(s * 0.25), int(s * 0.25), int(s * 0.75), int(s * 0.75)), fill=col, width=w)
        d.line((int(s * 0.75), int(s * 0.25), int(s * 0.25), int(s * 0.75)), fill=col, width=w)
    elif name == "pause":
        d.line((int(s * 0.36), int(s * 0.26), int(s * 0.36), int(s * 0.74)), fill=col, width=w)
        d.line((int(s * 0.64), int(s * 0.26), int(s * 0.64), int(s * 0.74)), fill=col, width=w)
    elif name == "check":
        d.line((int(s * 0.22), int(s * 0.52), int(s * 0.42), int(s * 0.72)), fill=col, width=w)
        d.line((int(s * 0.42), int(s * 0.72), int(s * 0.78), int(s * 0.30)), fill=col, width=w)
    image = img.resize((size, size), Image.Resampling.LANCZOS)
    photo = ImageTk.PhotoImage(image, master=master)
    SVG_ICON_CACHE[key] = photo
    return photo


PRIVATE_FONT_FLAG = 0x10
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
DEFAULT_HOTKEY = "Ctrl + Shift + J"
HOTKEY_TEXT = DEFAULT_HOTKEY


def parse_hotkey(text):
    parts = [part.strip().lower() for part in text.split("+") if part.strip()]
    modifiers = {"ctrl": 0x0002, "control": 0x0002, "alt": 0x0001, "shift": 0x0004, "win": 0x0008}
    mod = 0
    keys = []
    for part in parts:
        if part in modifiers:
            mod |= modifiers[part]
        else:
            keys.append(part)
    if not mod or len(keys) != 1:
        raise ValueError("Добавь хотя бы один модификатор и одну клавишу")
    key = keys[0].replace(" ", "")
    special = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B,
               "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
               "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28,
               "left": 0x25, "right": 0x27, "printscreen": 0x2C,
               "backspace": 0x08, "capslock": 0x14, "numlock": 0x90,
               "comma": 0xBC, "period": 0xBE, "slash": 0xBF, "backslash": 0xDC,
               "semicolon": 0xBA, "equal": 0xBB, "minus": 0xBD,
               "bracketleft": 0xDB, "bracketright": 0xDD, "grave": 0xC0,
               "kp_0": 0x60, "kp_1": 0x61, "kp_2": 0x62, "kp_3": 0x63,
               "kp_4": 0x64, "kp_5": 0x65, "kp_6": 0x66, "kp_7": 0x67,
               "kp_8": 0x68, "kp_9": 0x69, "kp_multiply": 0x6A,
               "kp_add": 0x6B, "kp_subtract": 0x6D, "kp_decimal": 0x6E,
               "kp_divide": 0x6F}
    if key in special:
        vk = special[key]
        label = {"escape": "Esc", "esc": "Esc", "return": "Enter", "delete": "Delete", "insert": "Insert", "pageup": "Page Up", "pagedown": "Page Down", "printscreen": "Print Screen"}.get(key, key.title())
    elif len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
        label = key.upper()
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
        label = key.upper()
    else:
        raise ValueError("Эта клавиша не поддерживается")
    names = [(0x0002, "Ctrl"), (0x0001, "Alt"), (0x0004, "Shift"), (0x0008, "Win")]
    return mod, vk, " + ".join([name for bit, name in names if mod & bit] + [label])


class _WinPoint(ctypes.Structure):
    _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))


class _WinSize(ctypes.Structure):
    _fields_ = (("cx", ctypes.c_long), ("cy", ctypes.c_long))


class _WinBlend(ctypes.Structure):
    _fields_ = (
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    )


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = (
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    )


class _RgbQuad(ctypes.Structure):
    _fields_ = (
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    )


class _BitmapInfo(ctypes.Structure):
    _fields_ = (("bmiHeader", _BitmapInfoHeader), ("bmiColors", _RgbQuad * 1))


class DownloadCancelled(Exception):
    pass


class RoundedPanel(tk.Frame):
    """A Tk panel with a 4x supersampled anti-aliased rounded background and soft border."""

    def __init__(
        self,
        master,
        padding=20,
        fill=PANEL_SURFACE,
        outline="#ffffff",
        radius=26,
        surface=APP_SURFACE,
        shadow=True,
        shadow_pad=12,
        padding_x=None,
        padding_y=None,
        **kwargs,
    ):
        super().__init__(master, bg=surface, bd=0, highlightthickness=0, **kwargs)
        self.fill = fill
        self.outline = outline
        self.radius = radius
        self.surface = surface
        self.shadow = shadow
        self.shadow_pad = shadow_pad if shadow else 0
        self.canvas = tk.Canvas(self, bg=surface, bd=0, highlightthickness=0)
        self.canvas.place(relwidth=1, relheight=1)
        pad_x = (padding if padding_x is None else padding_x) + self.shadow_pad
        pad_y = (padding if padding_y is None else padding_y) + self.shadow_pad
        self.body = tk.Frame(self, bg=fill, bd=0, highlightthickness=0)
        self.body.pack(fill="both", expand=True, padx=pad_x, pady=pad_y)
        self._draw_pending = None
        self._bg_photo = None
        self.bind("<Configure>", self.schedule_draw_panel)

    def schedule_draw_panel(self, event):
        self._last_panel_size = (event.width, event.height)
        if self._draw_pending is None:
            self._draw_pending = self.after(18, self.draw_panel)

    def draw_panel(self):
        self._draw_pending = None
        w, h = getattr(self, "_last_panel_size", (self.winfo_width(), self.winfo_height()))
        if w < 12 or h < 12:
            return
        lighter = "#ffffff"
        pil_img = render_aa_gradient_pill(
            w, h, radius=self.radius,
            top=lighter, bottom=self.fill, outline=self.outline,
            shadow=self.shadow, shadow_pad=self.shadow_pad,
        )
        self._bg_photo = ImageTk.PhotoImage(pil_img, master=self.canvas)
        self.canvas.delete("panel")
        self.canvas.create_image(0, 0, image=self._bg_photo, anchor="nw", tags="panel")
        self.canvas.tk.call("lower", self.canvas._w)


class RoundedButton(tk.Canvas):
    def __init__(
        self,
        master,
        text,
        command,
        width=184,
        height=56,
        fill="#e7effb",
        hover="#dbe8fa",
        foreground="#32425f",
        accent=False,
        background=PANEL_SURFACE,
    ):
        super().__init__(
            master, width=width, height=height, bg=background, bd=0, highlightthickness=0
        )
        self.text, self.command = text, command
        self.width, self.height = width, height
        self.accent = accent
        self.fill = "#2581f6" if accent else fill
        self.hover = "#1972e6" if accent else hover
        self.foreground = foreground
        self.active = False
        self._bg_photo = None
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", lambda _event: self.command())
        self.bind("<Return>", lambda _event: self.command())
        self.configure(cursor="hand2", takefocus=True)

    def draw(self):
        w, h = max(12, self.winfo_width()), max(12, self.winfo_height())
        r = h // 2 if self.accent else 16
        fill = (self.hover if self.active else self.fill)
        pil_img = render_aa_pill(
            w,
            h,
            radius=r,
            fill=fill,
            outline=None,
            shadow_color="#2581f6" if self.accent else None,
            shadow_blur=5 if self.accent else 0,
            shadow_offset=(0, 2) if self.accent else (0, 0),
            pad=2 if self.accent else 0,
        )
        self._bg_photo = ImageTk.PhotoImage(pil_img, master=self)
        self.delete("all")
        self.create_image(0, 0, image=self._bg_photo, anchor="nw")
        button_font = (
            APP_FONT,
            14 if self.accent else 12,
            "bold" if self.accent else "normal",
        )
        if self.text.startswith("↓"):
            self._button_icon = svg_icon_photo(self, "download", 20, self.foreground)
            clean_label = self.text.replace("↓", "", 1).strip()
            if self._button_icon:
                self.create_image(w / 2 - 42, h / 2, image=self._button_icon)
            self.create_text(
                w / 2 + 14, h / 2, text=clean_label, fill=self.foreground, font=button_font
            )
        else:
            self.create_text(w / 2, h / 2, text=self.text, fill=self.foreground, font=button_font)

    def on_enter(self, _event):
        self.active = True
        self.draw()

    def on_leave(self, _event):
        self.active = False
        self.draw()


class RoundedTab(tk.Canvas):
    def __init__(self, master, text, icon, command, width=164):
        super().__init__(
            master,
            width=width,
            height=66,
            bg=APP_SURFACE,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
        )
        self.text, self.icon, self.command = text, icon, command
        self.icon_name = {"↓": "download", "◷": "clock-3", "⚙": "settings"}.get(icon, "circle")
        self.selected = False
        self.hover = False
        self._bg_photo = None
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Button-1>", lambda _event: self.command())
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.draw()

    def set_selected(self, selected):
        self.selected = selected
        self.draw()

    def draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 48 or h < 48:
            return
        self.delete("all")
        if self.selected:
            pil_img = render_aa_pill(
                w,
                h,
                radius=(h - 16) // 2,
                fill="#eff6ff",
                outline="#ffffff",
                outline_width=2,
                shadow_color="#7da7dc",
                shadow_blur=6,
                shadow_offset=(0, 3),
                pad=9,
            )
        else:
            fill = "#dfeafa" if self.hover else "#e6f0fc"
            pil_img = render_aa_pill(
                w,
                h,
                radius=(h - 16) // 2,
                fill=fill,
                outline=None,
                pad=9,
            )
        self._bg_photo = ImageTk.PhotoImage(pil_img, master=self)
        self.create_image(0, 0, image=self._bg_photo, anchor="nw")

        icon_color = "#2481f2" if self.selected else "#4b5a74"
        text_color = "#151f36" if self.selected else "#33425b"
        self._tab_icon = svg_icon_photo(self, self.icon_name, 19, icon_color)
        if self._tab_icon:
            self.create_image(36, h / 2, image=self._tab_icon)
        self.create_text(
            54,
            h / 2,
            text=self.text,
            anchor="w",
            fill=text_color,
            font=(APP_FONT, 13, "bold" if self.selected else "normal"),
        )

    def on_enter(self, _event):
        self.hover = True
        self.draw()

    def on_leave(self, _event):
        self.hover = False
        self.draw()


class RoundedSelect(tk.Canvas):
    def __init__(self, master, variable, values, width=180, height=42):
        super().__init__(
            master,
            width=width,
            height=height,
            bg=PANEL_SURFACE,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=True,
        )
        self.variable, self.values = variable, tuple(values)
        self.width, self.height = width, height
        self._bg_photo = None
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Button-1>", self.open_menu)
        self.bind("<Return>", self.open_menu)
        self.menu = tk.Menu(
            self,
            tearoff=0,
            bg=PANEL_SURFACE,
            fg="#23314b",
            activebackground="#dcecff",
            activeforeground="#126ed1",
            bd=0,
            relief="flat",
            font=(APP_FONT, 11),
        )
        for value in self.values:
            self.menu.add_radiobutton(
                label=value, variable=self.variable, value=value, command=self.draw
            )
        self.variable.trace_add("write", lambda *_: self.draw())

    def draw(self):
        w, h = max(12, self.winfo_width()), max(12, self.winfo_height())
        self.delete("all")
        pil_img = render_aa_pill(
            w, h, radius=h // 2, fill="#edf4ff", outline="#dbe7f6", outline_width=1
        )
        self._bg_photo = ImageTk.PhotoImage(pil_img, master=self)
        self.create_image(0, 0, image=self._bg_photo, anchor="nw")
        self.create_text(
            16, h / 2, text=self.variable.get(), anchor="w", fill="#2b3a56", font=(APP_FONT, 11)
        )
        self._chevron_photo = svg_icon_photo(self, "chevron-down", 14, "#607595")
        if self._chevron_photo:
            self.create_image(w - 18, h / 2, image=self._chevron_photo)

    def open_menu(self, _event=None):
        try:
            self.menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())
        finally:
            self.menu.grab_release()


class RoundedToggle(tk.Frame):
    def __init__(
        self, master, text, variable, command, width=320, background=PANEL_SURFACE, height=36
    ):
        super().__init__(
            master,
            bg=background,
            bd=0,
            highlightthickness=0,
            width=width,
            height=height,
            cursor="hand2",
        )
        self.pack_propagate(False)
        self.variable, self.command = variable, command
        self.background = background
        self._switch_photo = None
        self.switch = tk.Canvas(
            self, width=52, height=30, bg=background, bd=0, highlightthickness=0
        )
        self.switch.pack(side="left", padx=(0, 10))
        self.label = tk.Label(
            self,
            text=text,
            bg=background,
            fg="#2c3a52",
            font=(APP_FONT, 13),
            anchor="w",
        )
        self.label.pack(side="left", fill="x", expand=True)
        for widget in (self, self.switch, self.label):
            widget.bind("<Button-1>", self.toggle)
            widget.configure(cursor="hand2")
        self.variable.trace_add("write", lambda *_: self.draw())
        self.draw()

    def draw(self):
        self.switch.delete("all")
        on = self.variable.get()
        scale = AA_RENDER_SCALE
        w, h = 52 * scale, 30 * scale
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        track = _parse_hex("#3185f7" if on else "#cdd8e8")
        d.rounded_rectangle((2 * scale, 2 * scale, w - 2 * scale, h - 2 * scale), radius=13 * scale, fill=track)
        knob_x = (26 if on else 5) * scale
        knob_y = 5 * scale
        knob_d = 20 * scale
        # Subtle shadow under knob
        d.ellipse((knob_x - scale, knob_y + scale, knob_x + knob_d + scale, knob_y + knob_d + scale), fill=(20, 45, 90, 35))
        d.ellipse((knob_x, knob_y, knob_x + knob_d, knob_y + knob_d), fill=(255, 255, 255, 255))
        out = img.resize((52, 30), Image.Resampling.LANCZOS)
        self._switch_photo = ImageTk.PhotoImage(out, master=self.switch)
        self.switch.create_image(0, 0, image=self._switch_photo, anchor="nw")

    def toggle(self, _event=None):
        self.variable.set(not self.variable.get())
        self.command()


class RoundedProgress(tk.Canvas):
    def __init__(self, master, height=10):
        super().__init__(master, height=height, bg=PANEL_SURFACE, bd=0, highlightthickness=0)
        self.value = 0.0
        self._bar_photo = None
        self.bind("<Configure>", lambda _event: self.draw())

    def set(self, value):
        self.value = max(0.0, min(100.0, float(value)))
        self.draw()

    def draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 8 or h < 4:
            return
        self.delete("all")
        scale = AA_RENDER_SCALE
        sw, sh = w * scale, h * scale
        r = sh // 2
        img = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, sw - 1, sh - 1), radius=r, fill=_parse_hex("#dfe7f4"))
        fw = int(sw * self.value / 100.0)
        if fw > 0:
            fw = max(sh, fw)
            d.rounded_rectangle((0, 0, min(sw - 1, fw), sh - 1), radius=r, fill=_parse_hex("#2581f6"))
        out = img.resize((w, h), Image.Resampling.LANCZOS)
        self._bar_photo = ImageTk.PhotoImage(out, master=self)
        self.create_image(0, 0, image=self._bar_photo, anchor="nw")


class PloopyScrollbar(tk.Canvas):
    """Visible rounded scrollbar thumb for the file lists."""

    def __init__(self, master, command, background=PANEL_SURFACE, width=16):
        super().__init__(master, width=width, bg=background, bd=0, highlightthickness=0, cursor="hand2")
        self.command = command
        self.first, self.last = 0.0, 1.0
        self.thumb_top, self.thumb_height = 0, 0
        self._thumb_image = None
        self._drag_offset = None
        self.bind("<Configure>", lambda _e: self._draw_thumb())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)

    def set(self, first, last):
        self.first, self.last = float(first), float(last)
        self._draw_thumb()

    def _draw_thumb(self):
        height = self.winfo_height()
        if height < 24:
            return
        self.delete("thumb")
        self.thumb_height = max(42, min(height, round(height * max(0.04, self.last - self.first))))
        travel = max(0, height - self.thumb_height)
        self.thumb_top = round(travel * self.first / max(0.001, 1 - (self.last - self.first)))
        self._thumb_image = ImageTk.PhotoImage(
            render_aa_pill(10, self.thumb_height, radius=5, fill="#b8d0ef"), master=self
        )
        self.create_image(self.winfo_width() // 2, self.thumb_top, image=self._thumb_image, anchor="n", tags="thumb")

    def _press(self, event):
        if self.thumb_top <= event.y <= self.thumb_top + self.thumb_height:
            self._drag_offset = event.y - self.thumb_top
        else:
            self._drag_offset = self.thumb_height // 2
            self._move_thumb(event.y)

    def _drag(self, event):
        self._move_thumb(event.y)

    def _move_thumb(self, y):
        travel = max(1, self.winfo_height() - self.thumb_height)
        fraction = (y - (self._drag_offset or 0)) / travel
        self.command("moveto", max(0.0, min(1.0, fraction)))



class CircleActionButton(tk.Canvas):
    """Smooth circular icon button for pause / cancel / status in download rows."""

    def __init__(self, master, icon_name, command=None, fill="#eef3fb", hover="#e1ebf9", fg="#475670", size=36):
        super().__init__(
            master,
            width=size,
            height=size,
            bg=PANEL_SURFACE,
            bd=0,
            highlightthickness=0,
            cursor="hand2" if command else "arrow",
        )
        self.icon_name = icon_name
        self.command = command
        self.fill = fill
        self.hover_fill = hover
        self.fg = fg
        self.size = size
        self.hover = False
        self._bg_photo = None
        self._icon_photo = None
        self.bind("<Configure>", lambda _e: self.draw())
        if command:
            self.bind("<Enter>", self.on_enter)
            self.bind("<Leave>", self.on_leave)
            self.bind("<Button-1>", lambda _e: self.command())
        self.draw()

    def set_icon(self, icon_name: str):
        self.icon_name = icon_name
        self.draw()

    def draw(self):
        s = self.size
        self.delete("all")
        bg_col = self.hover_fill if (self.hover and self.command) else self.fill
        pil_img = render_aa_pill(s, s, radius=s // 2, fill=bg_col)
        self._bg_photo = ImageTk.PhotoImage(pil_img, master=self)
        self.create_image(0, 0, image=self._bg_photo, anchor="nw")
        self._icon_photo = svg_icon_photo(self, self.icon_name, 16, self.fg)
        if self._icon_photo:
            self.create_image(s / 2, s / 2, image=self._icon_photo)

    def on_enter(self, _e):
        self.hover = True
        self.draw()

    def on_leave(self, _e):
        self.hover = False
        self.draw()


class WindowControl(tk.Canvas):
    """Compact title-bar button using Lucide SVG glyphs."""

    def __init__(self, master, kind, command, background=APP_SURFACE):
        super().__init__(
            master,
            width=44,
            height=38,
            bg=background,
            bd=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=True,
        )
        self.kind, self.command, self.background = kind, command, background
        self.hover = False
        self._bg_photo = None
        self.bind("<Configure>", lambda _event: self.draw())
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)
        self.bind("<Button-1>", lambda _event: self.command())
        self.bind("<Return>", lambda _event: self.command())
        self.draw()

    def draw(self):
        w, h = max(4, self.winfo_width()), max(4, self.winfo_height())
        self.delete("all")
        if self.hover:
            fill = "#ffe8eb" if self.kind == "close" else "#e3edfb"
            pil_img = render_aa_pill(w, h, radius=min(w, h) // 2, fill=fill, pad=2)
            self._bg_photo = ImageTk.PhotoImage(pil_img, master=self)
            self.create_image(0, 0, image=self._bg_photo, anchor="nw")
        color = "#3b4861" if not (self.hover and self.kind == "close") else "#c84759"
        cx, cy = w / 2, h / 2
        icon_name = {"minimize": "minus", "maximize": "square", "close": "x"}[self.kind]
        self._control_icon = svg_icon_photo(self, icon_name, 17, color)
        if self._control_icon:
            self.create_image(cx, cy, image=self._control_icon)

    def on_enter(self, _event):
        self.hover = True
        self.draw()

    def on_leave(self, _event):
        self.hover = False
        self.draw()


class FolderAction(tk.Frame):
    """Folder action with clean SVG glyphs."""

    def __init__(self, master, command, background=APP_SURFACE):
        super().__init__(master, bg=background, bd=0, highlightthickness=0, cursor="hand2")
        self.background = background
        color = "#5c6d87"
        self.icon_photo = svg_icon_photo(self, "folder-open", 19, color)
        self.icon = tk.Label(self, image=self.icon_photo, bg=background, bd=0, cursor="hand2")
        self.icon.pack(side="left", padx=(0, 8))
        self.label = tk.Label(
            self,
            text="Открыть папку загрузок",
            bg=background,
            fg="#47566f",
            font=(APP_FONT, 13),
            cursor="hand2",
        )
        self.label.pack(side="left")
        self.external_photo = svg_icon_photo(self, "external-link", 16, color)
        self.external = tk.Label(
            self, image=self.external_photo, bg=background, bd=0, cursor="hand2"
        )
        self.external.pack(side="left", padx=(8, 0))
        for widget in (self, self.icon, self.label, self.external):
            widget.bind("<Button-1>", lambda _event: command())


class DownloaderApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.private_font_paths = []
        self.load_private_fonts()
        self.root.title("Ploopy")
        window_width, window_height = 880, 940
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_x = max(0, (screen_width - window_width) // 2)
        window_y = max(0, (screen_height - window_height) // 2)
        self.root.geometry(f"{window_width}x{window_height}+{window_x}+{window_y}")
        self.root.minsize(820, 860)
        try:
            self.root.iconbitmap(str(APP_DIR / "Ploopy.ico"))
        except tk.TclError:
            pass
        self.ui_customization = load_ui_customization()
        self.ui_config_stamp = self.ui_config_mtime()
        self.root.title(self.ui_customization["brand"])
        self.settings = self.load_settings()
        global HOTKEY_TEXT
        HOTKEY_TEXT = self.settings.get("download_hotkey", DEFAULT_HOTKEY)
        self.events: queue.Queue = queue.Queue()
        self.jobs: queue.Queue[str] = queue.Queue()
        self.download_dir = tk.StringVar(value=str(DEFAULT_DIR))
        self.browser = tk.StringVar(value=self.settings.get("browser", "Без авторизации"))
        self.browser.trace_add("write", self.persist_browser_choice)
        self.quality = tk.StringVar(value="720p — быстрее")
        self.progress_value = tk.DoubleVar(value=0)
        self.progress_title = tk.StringVar(value="Нет активной загрузки")
        self.progress_percent = tk.StringVar(value="—")
        self.status_detail = tk.StringVar(value="Готов к работе")
        self.start_minimized_var = tk.BooleanVar(value=self.settings.get("start_minimized", True))
        self.run_at_login_var = tk.BooleanVar(value=self.settings.get("run_at_login", False))
        self.sound_var = tk.BooleanVar(value=self.settings.get("sound", True))
        self.download_hotkey_var = tk.StringVar(value=self.settings.get("download_hotkey", DEFAULT_HOTKEY))
        self.download_rows = {}
        self.current_url = None
        self.current_process = None
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.cancel_requested = False
        self.history = self.load_history()
        self.is_downloading = False
        self.overlay = None
        self.overlay_canvas = None
        self.overlay_hwnd = None
        self.overlay_dib_dc = None
        self.overlay_dib_bitmap = None
        self.overlay_dib_previous = None
        self.overlay_dib_bits = None
        self.overlay_dib_size = None
        self.overlay_border_base = None
        self.overlay_border_cache_key = None
        self.overlay_border_cache = None
        self.overlay_alpha_failed = False
        self.overlay_animation_frames = []
        self.overlay_animation_loaded = False
        self.overlay_frame_index = 0
        self.overlay_next_frame_at = 0.0
        self.overlay_percent = None
        self.overlay_done = False
        self.overlay_after_id = None
        self.overlay_hide_after_id = None
        self.overlay_redraw_after_id = None
        self.overlay_last_draw_at = 0.0
        self.overlay_generation = 0
        self.tray_icon = None
        self.tray_ready = threading.Event()
        self.hotkey_thread = None
        self.update_check_in_progress = False
        self.visual_edit_mode = False
        self.element_edits = self.load_element_edits()
        self._edit_drag_widget = None
        self._edit_drag_start = None
        self._edit_drag_kind = None
        self._edit_canvas_item = None
        self._edit_original_bindtags = {}
        self._setup_style()
        self._build_ui()
        self.install_visual_editor()
        self.root.after(500, self.apply_saved_element_edits)
        # Use the normal Windows frame so the operating system handles resize
        # and maximize smoothly instead of redrawing a custom window skin.
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.start_hidden_requested = "--tray" in sys.argv or (
            self.settings.get("start_minimized", True) and "--show" not in sys.argv
        )
        self.start_tray()
        self.start_global_hotkey()
        threading.Thread(
            target=self.preload_overlay_animation, daemon=True, name="ploopy-overlay-assets"
        ).start()
        self.root.after(150, self.process_events)
        self.root.after(900, self.poll_ui_customization)
        self.root.after(15000, lambda: self.check_for_updates(manual=False))
        threading.Thread(target=self.worker, daemon=True).start()

    @staticmethod
    def load_element_edits():
        defaults = {"positions": {}, "texts": {}}
        try:
            saved = json.loads(ELEMENT_EDITS_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                for key in defaults:
                    if isinstance(saved.get(key), dict):
                        defaults[key] = saved[key]
        except (OSError, ValueError, TypeError):
            pass
        return defaults

    def save_element_edits(self):
        try:
            ELEMENT_EDITS_PATH.write_text(
                json.dumps(self.element_edits, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except OSError as exc:
            self.status_detail.set(f"Не удалось сохранить оформление: {exc}")
            return False

    @staticmethod
    def ui_config_mtime():
        try:
            return UI_CONFIG_PATH.stat().st_mtime_ns
        except OSError:
            return None

    def save_ui_customization(self):
        try:
            UI_CONFIG_PATH.write_text(
                json.dumps(self.ui_customization, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.ui_config_stamp = self.ui_config_mtime()
            return True
        except OSError as exc:
            self.status_detail.set(f"Не удалось сохранить настройки интерфейса: {exc}")
            return False

    def install_visual_editor(self):
        """Enable persistent in-app layout and label editing with Ctrl+Shift+E."""
        # Ctrl+Shift+E is registered once through Windows RegisterHotKey below.
        # Avoid also binding it in Tk: when the app has focus both handlers
        # would run for one press and immediately toggle the mode back off.
        self.root.bind_all("<ButtonPress-1>", self._edit_mouse_down, add="+")
        self.root.bind_all("<B1-Motion>", self.edit_mouse_move, add="+")
        self.root.bind_all("<ButtonRelease-1>", self.edit_mouse_up, add="+")
        self.root.bind_all("<Double-Button-1>", self.edit_text_at_pointer, add="+")

    def activate_visual_edit_mode(self, _event=None):
        if not self.root.winfo_viewable():
            self.show_window()
        return self.toggle_visual_edit_mode(_event)

    def _set_edit_bindtags(self, widget, enabled):
        try:
            tags = widget.bindtags()
            if enabled:
                if widget not in self._edit_original_bindtags:
                    self._edit_original_bindtags[widget] = tags
                tags = tuple(tag for tag in tags if tag != "all")
                widget.bindtags(("all",) + tags)
            else:
                original = self._edit_original_bindtags.pop(widget, None)
                if original:
                    widget.bindtags(original)
            for child in widget.winfo_children():
                self._set_edit_bindtags(child, enabled)
        except tk.TclError:
            pass

    def toggle_visual_edit_mode(self, _event=None):
        if getattr(self, "_edit_modal_active", False):
            return None
        self.visual_edit_mode = not self.visual_edit_mode
        if self.visual_edit_mode:
            self._set_edit_bindtags(self.root, True)
            self.edit_mode_badge.pack(side="right", padx=10)
            self.root.title(f"{self.ui_customization['brand']} · режим редактирования")
            self.root.focus_set()
            self.status_detail.set(
                "Перетаскивай элементы мышью; двойной клик по тексту — изменить."
            )
        else:
            self._edit_drag_widget = None
            self._edit_drag_start = None
            self._edit_drag_kind = None
            self._edit_canvas_item = None
            self._set_edit_bindtags(self.root, False)
            self._edit_original_bindtags.clear()
            self.edit_mode_badge.pack_forget()
            self.root.title(self.ui_customization["brand"])
            self.save_element_edits()
            self.status_detail.set("Изменения оформления сохранены")
        return "break"

    def _edit_mouse_down(self, event):
        if not self.visual_edit_mode or getattr(self, "_edit_modal_active", False):
            return None
        widget = event.widget
        if widget is self.root or not getattr(widget, "master", None):
            return "break"
        manager = widget.winfo_manager()
        self._edit_drag_kind = manager if manager == "canvas" else "widget"
        self._edit_canvas_item = self._canvas_window_item(widget) if manager == "canvas" else None
        if manager == "canvas" and self._edit_canvas_item is None:
            self.edit_mode_badge.configure(text="Не удалось выбрать элемент холста")
            return "break"
        if manager not in ("", "pack", "grid", "place", "canvas"):
            self.edit_mode_badge.configure(text="Этот тип элемента нельзя переместить")
            return "break"
        if manager == "canvas":
            coords = widget.master.coords(self._edit_canvas_item)
            widget_x, widget_y = coords[:2] if len(coords) >= 2 else (0, 0)
        else:
            widget_x, widget_y = widget.winfo_x(), widget.winfo_y()
        self._edit_drag_widget = widget
        self._edit_drag_start = (
            event.x_root,
            event.y_root,
            widget_x,
            widget_y,
            max(1, widget.winfo_width()),
            max(1, widget.winfo_height()),
        )
        self.edit_mode_badge.configure(
            text=f"Выбрано: {widget.winfo_class()} · тяни мышью · Ctrl+Shift+E — выход"
        )
        return "break"

    def _place_edited_widget(self, widget, x, y, width, height, save=True):
        try:
            parent = widget.master
            parent_width = max(1, parent.winfo_width())
            parent_height = max(1, parent.winfo_height())
            x = max(0, min(parent_width - width, int(x)))
            y = max(0, min(parent_height - height, int(y)))
            manager = widget.winfo_manager()
            if manager == "pack":
                widget.pack_forget()
            elif manager == "grid":
                widget.grid_forget()
            elif manager not in ("", "place"):
                return False
            widget.place(
                relx=x / parent_width,
                rely=y / parent_height,
                x=0,
                y=0,
                width=width,
                height=height,
                anchor="nw",
            )
            if save:
                self.element_edits["positions"][widget._w] = {
                    "parent": parent._w,
                    "relx": x / parent_width,
                    "rely": y / parent_height,
                    "width": width,
                    "height": height,
                }
            return True
        except (tk.TclError, AttributeError):
            return False

    @staticmethod
    def _canvas_window_item(widget):
        canvas = getattr(widget, "master", None)
        if not isinstance(canvas, tk.Canvas):
            return None
        try:
            for item in canvas.find_all():
                if canvas.type(item) == "window" and canvas.itemcget(item, "window") == widget._w:
                    return item
        except tk.TclError:
            pass
        return None

    def edit_mouse_move(self, event):
        if (
            not self.visual_edit_mode
            or getattr(self, "_edit_modal_active", False)
            or not self._edit_drag_widget
            or not self._edit_drag_start
        ):
            return None
        start_x, start_y, widget_x, widget_y, width, height = self._edit_drag_start
        dx, dy = event.x_root - start_x, event.y_root - start_y
        if abs(dx) + abs(dy) > 2:
            if self._edit_drag_kind == "canvas":
                canvas = self._edit_drag_widget.master
                canvas.coords(self._edit_canvas_item, widget_x + dx, widget_y + dy)
                canvas.configure(scrollregion=canvas.bbox("all"))
            else:
                self._place_edited_widget(
                    self._edit_drag_widget, widget_x + dx, widget_y + dy, width, height, save=False
                )
        return "break"

    def edit_mouse_up(self, _event=None):
        if not self.visual_edit_mode or getattr(self, "_edit_modal_active", False):
            return None
        widget = self._edit_drag_widget
        if widget:
            try:
                if self._edit_drag_kind == "canvas":
                    canvas = widget.master
                    coords = canvas.coords(self._edit_canvas_item)
                    self.element_edits["positions"][widget._w] = {
                        "parent": canvas._w,
                        "manager": "canvas",
                        "x": coords[0] if coords else 0,
                        "y": coords[1] if len(coords) > 1 else 0,
                        "width": widget.winfo_width(),
                        "height": widget.winfo_height(),
                    }
                    canvas.configure(scrollregion=canvas.bbox("all"))
                    self.save_element_edits()
                elif widget.winfo_manager() == "place":
                    info = widget.place_info()
                    parent = widget.master
                    self.element_edits["positions"][widget._w] = {
                        "parent": parent._w,
                        "manager": "place",
                        "relx": float(info.get("relx", 0) or 0),
                        "rely": float(info.get("rely", 0) or 0),
                        "width": widget.winfo_width(),
                        "height": widget.winfo_height(),
                    }
                    self.save_element_edits()
            except (tk.TclError, TypeError, ValueError):
                pass
        self._edit_drag_widget = None
        self._edit_drag_start = None
        self._edit_drag_kind = None
        self._edit_canvas_item = None
        return "break"

    def _read_editable_text(self, widget):
        if widget is getattr(self, "heading_one_label", None):
            return self.ui_customization.get("heading_one", "")
        if widget is getattr(self, "heading_two_label", None):
            return self.ui_customization.get("heading_two", "")
        if widget is getattr(self, "url_entry", None):
            placeholder = self.ui_customization.get("placeholder", "")
            return placeholder if widget.get() == placeholder else None
        if isinstance(widget, (tk.Label, ttk.Label, tk.Button, ttk.Button)):
            try:
                variable = widget.cget("textvariable")
                if variable:
                    return str(self.root.getvar(variable))
                return str(widget.cget("text"))
            except tk.TclError:
                return None
        if isinstance(widget, tk.Canvas) and isinstance(getattr(widget, "text", None), str):
            return widget.text
        return None

    def _set_editable_text(self, widget, text):
        if widget is getattr(self, "heading_one_label", None):
            self.ui_customization["heading_one"] = text
            self._set_headline(
                text,
                self.ui_customization.get("heading_two", ""),
                int(self.ui_customization.get("heading_size", 60)),
            )
            self.description_label.place_configure(y=self.headline_image.height() + 10)
            return self.save_ui_customization()
        if widget is getattr(self, "heading_two_label", None):
            self.ui_customization["heading_two"] = text
            self._set_headline(
                self.ui_customization.get("heading_one", ""),
                text,
                int(self.ui_customization.get("heading_size", 60)),
            )
            self.description_label.place_configure(y=self.headline_image.height() + 10)
            return self.save_ui_customization()
        if widget is getattr(self, "description_label", None):
            self.ui_customization["description"] = text
            widget.configure(text=text)
            return self.save_ui_customization()
        if widget is getattr(self, "brand_label", None):
            self.ui_customization["brand"] = text
            widget.configure(text=text)
            self.root.title(text)
            return self.save_ui_customization()
        if widget is getattr(self, "url_entry", None):
            old_placeholder = self.ui_customization.get("placeholder", "")
            self.ui_customization["placeholder"] = text
            if widget.get() == old_placeholder:
                widget.delete(0, "end")
                widget.insert(0, text)
            return self.save_ui_customization()
        if widget is getattr(self, "download_button", None):
            self.ui_customization["download_button"] = text
            widget.text = text
            widget.draw()
            return self.save_ui_customization()
        if isinstance(widget, (tk.Label, ttk.Label, tk.Button, ttk.Button)):
            try:
                variable = widget.cget("textvariable")
                if variable:
                    self.root.setvar(variable, text)
                    return True
                widget.configure(text=text)
                return True
            except tk.TclError:
                return False
        if isinstance(widget, tk.Canvas) and isinstance(getattr(widget, "text", None), str):
            widget.text = text
            draw = getattr(widget, "draw", None)
            if callable(draw):
                draw()
                return True
        return False

    def edit_text_at_pointer(self, event):
        if not self.visual_edit_mode or getattr(self, "_edit_modal_active", False):
            return None
        widget = event.widget
        initial = self._read_editable_text(widget)
        if initial is None:
            return "break"
        self._edit_modal_active = True
        try:
            value = simpledialog.askstring(
                "Изменить текст",
                "Новый текст:",
                initialvalue=initial,
                parent=self.root,
            )
        finally:
            self._edit_modal_active = False
        if value is not None and self._set_editable_text(widget, value):
            persistent_setting = widget in (
                getattr(self, "heading_one_label", None),
                getattr(self, "heading_two_label", None),
                getattr(self, "description_label", None),
                getattr(self, "brand_label", None),
                getattr(self, "url_entry", None),
                getattr(self, "download_button", None),
            )
            if persistent_setting:
                self.element_edits["texts"].pop(widget._w, None)
                self.save_element_edits()
            else:
                self.element_edits["texts"][widget._w] = value
                self.save_element_edits()
            self.edit_mode_badge.configure(text="Текст сохранён · Ctrl+Shift+E — выход")
        return "break"

    def apply_saved_element_edits(self, subtree=None):
        """Restore text and drag positions from the user's local edit file."""
        try:
            self.root.update_idletasks()
        except tk.TclError:
            return
        widgets = {}

        def collect(widget):
            widgets[widget._w] = widget
            try:
                for child in widget.winfo_children():
                    collect(child)
            except tk.TclError:
                pass

        collect(subtree or self.root)
        if subtree is not None:
            # A new download/history row may be created after startup.
            self.root.update_idletasks()
        for path, text in self.element_edits.get("texts", {}).items():
            widget = widgets.get(path)
            if widget is not None:
                self._set_editable_text(widget, str(text))
        self.root.update_idletasks()
        for path, record in self.element_edits.get("positions", {}).items():
            widget = widgets.get(path)
            if widget is None or widget.master._w != record.get("parent"):
                continue
            try:
                if record.get("manager") == "canvas":
                    item = self._canvas_window_item(widget)
                    if item is not None:
                        canvas = widget.master
                        canvas.coords(item, float(record.get("x", 0)), float(record.get("y", 0)))
                        canvas.configure(scrollregion=canvas.bbox("all"))
                    continue
                width = max(1, int(record.get("width", widget.winfo_width())))
                height = max(1, int(record.get("height", widget.winfo_height())))
                parent = widget.master
                self._place_edited_widget(
                    widget,
                    float(record.get("relx", 0)) * max(1, parent.winfo_width()),
                    float(record.get("rely", 0)) * max(1, parent.winfo_height()),
                    width,
                    height,
                    save=False,
                )
            except (tk.TclError, TypeError, ValueError):
                continue
        self.root.update_idletasks()

    @staticmethod
    def load_settings():
        defaults = {
            "start_minimized": True,
            "run_at_login": False,
            "sound": True,
            "browser": "Автоматически",
            "download_hotkey": DEFAULT_HOTKEY,
        }
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            for key in ("start_minimized", "run_at_login", "sound"):
                defaults[key] = bool(saved.get(key, defaults[key]))
            browser = saved.get("browser", defaults["browser"])
            if browser in ("Автоматически", "Без авторизации", "Chrome", "Edge", "Firefox", "Brave", "Opera"):
                defaults["browser"] = browser
            try:
                _modifiers, _vk, defaults["download_hotkey"] = parse_hotkey(saved.get("download_hotkey", DEFAULT_HOTKEY))
            except (ValueError, AttributeError):
                pass
        except (OSError, ValueError, TypeError):
            pass
        return defaults

    def persist_browser_choice(self, *_args):
        browser = self.browser.get()
        if browser not in ("Автоматически", "Без авторизации", "Chrome", "Edge", "Firefox", "Brave", "Opera"):
            return
        self.settings["browser"] = browser
        try:
            self.save_settings()
        except OSError as exc:
            self.status_detail.set(f"Не удалось сохранить браузер для входа: {exc}")

    def load_private_fonts(self):
        """Load bundled Manrope faces for this app process without installing system fonts."""
        if os.name == "nt":
            try:
                add_font = ctypes.windll.gdi32.AddFontResourceExW
                add_font.argtypes = (
                    ctypes.wintypes.LPCWSTR,
                    ctypes.wintypes.DWORD,
                    ctypes.c_void_p,
                )
                add_font.restype = ctypes.wintypes.INT
                for name in (
                    "Manrope-Regular.ttf",
                    "Manrope-Medium.ttf",
                    "Manrope-SemiBold.ttf",
                    "Manrope-Bold.ttf",
                ):
                    path = FONT_DIR / name
                    if path.is_file() and add_font(str(path), PRIVATE_FONT_FLAG, None):
                        self.private_font_paths.append(path)
            except (AttributeError, OSError):
                pass

        for font_name in (
            "TkDefaultFont",
            "TkTextFont",
            "TkFixedFont",
            "TkMenuFont",
            "TkHeadingFont",
            "TkCaptionFont",
            "TkSmallCaptionFont",
            "TkIconFont",
            "TkTooltipFont",
        ):
            try:
                tkfont.nametofont(font_name, root=self.root).configure(family=APP_FONT)
            except tk.TclError:
                pass

    def save_settings(self):
        SETTINGS_PATH.write_text(
            json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _setup_style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(APP_FONT, 10))
        style.configure("App.TFrame", background=APP_SURFACE)
        style.configure("Panel.TFrame", background=PANEL_SURFACE)
        style.configure(
            "Ploopy.Vertical.TScrollbar",
            troughcolor=PANEL_SURFACE,
            background="#d6e2f2",
            bordercolor=PANEL_SURFACE,
            lightcolor=PANEL_SURFACE,
            darkcolor=PANEL_SURFACE,
            arrowsize=0,
            width=6,
        )

    def _build_ui(self):
        """Build a responsive native Tk interface without image-based window skins."""
        self.root.overrideredirect(False)
        self.root.configure(bg=APP_SURFACE)
        self.root.minsize(780, 700)
        self.root.geometry("1100x950")

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=(APP_FONT, 10))
        style.configure("TFrame", background=APP_SURFACE)
        style.configure("Card.TFrame", background=PANEL_SURFACE, borderwidth=0, relief="flat")
        style.configure("TLabel", background=APP_SURFACE, foreground="#182238")
        style.configure("Muted.TLabel", background=APP_SURFACE, foreground="#63738f")
        style.configure("Title.TLabel", font=(APP_FONT, 20, "bold"), foreground="#182238")
        style.configure("Hero.TLabel", font=(APP_FONT, int(self.ui_customization.get("heading_size", 60)), "bold"), foreground="#101a31")
        style.configure("TButton", padding=(14, 9), relief="flat", borderwidth=0, focusthickness=0)
        style.configure("Accent.TButton", background="#2581f6", foreground="white", padding=(18, 10), relief="flat", borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#176fe0")])
        style.configure("TEntry", padding=10, relief="flat", borderwidth=0, fieldbackground=PANEL_SURFACE)
        style.configure("TCheckbutton", background=APP_SURFACE, foreground="#34435c")
        style.configure(
            "Ploopy.Vertical.TScrollbar", arrowsize=0, width=12, relief="flat",
            borderwidth=0, troughcolor="#edf3fc", background="#bfd3ee",
            bordercolor=PANEL_SURFACE, lightcolor="#bfd3ee", darkcolor="#bfd3ee",
        )
        style.configure("Horizontal.TProgressbar", troughcolor=APP_SURFACE, background="#2581f6", thickness=10, borderwidth=0, relief="flat")

        shell = ttk.Frame(self.root, padding=(30, 22))
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell)
        self.header = header
        header.pack(fill="x", pady=(0, 10))
        try:
            logo = Image.open(APP_DIR / "Ploopy.png").convert("RGBA")
            logo.thumbnail((42, 42), Image.Resampling.LANCZOS)
            self.logo_image = ImageTk.PhotoImage(logo, master=self.root)
            ttk.Label(header, image=self.logo_image).pack(side="left", padx=(0, 10))
        except OSError:
            pass
        self.brand_label = ttk.Label(header, text=self.ui_customization["brand"], style="Title.TLabel")
        self.brand_label.pack(side="left")
        self.edit_mode_badge = tk.Label(
            header,
            text="РЕДАКТОР · Ctrl+Shift+E — выход",
            bg="#e1efff",
            fg="#2478df",
            font=(APP_FONT, 9, "bold"),
            padx=10,
            pady=5,
        )

        hero = ttk.Frame(shell)
        hero.pack(fill="x", pady=(28, 0))
        copy = ttk.Frame(hero)
        copy.pack(side="left", fill="both", expand=True, anchor="w")
        self.heading_one_label = tk.Label(copy, bg=APP_SURFACE, bd=0, anchor="nw")
        self.heading_one_label.place(x=0, y=0, anchor="nw")
        self.heading_two_label = tk.Label(copy, bg=APP_SURFACE, bd=0, anchor="nw")
        self.heading_two_label.place(x=0, y=0, anchor="nw")
        self._set_headline(
            self.ui_customization["heading_one"], self.ui_customization["heading_two"],
            int(self.ui_customization.get("heading_size", 60)),
        )
        self.description_label = ttk.Label(copy, text=self.ui_customization["description"], style="Muted.TLabel", justify="left", font=(APP_FONT, 14))
        self.description_label.place(x=0, y=self.headline_image.height() + 10, anchor="nw")
        self.hero_area = ttk.Frame(hero, width=430, height=300)
        self.hero_area.pack(side="right", fill="y", padx=(18, 0))
        self.hero_area.pack_propagate(False)
        try:
            self.hero_art_source = trim_transparent_art(Image.open(Path(self.ui_customization["hero_image"])))
            # Keep the mascot as a shell-level overlay so it can extend below
            # the hero row and tuck slightly behind the link capsule.
            self.hero_art_label = tk.Label(shell, bg=APP_SURFACE, bd=0)
            self.hero_art_signature = None
            self.update_hero_art()
            hero.bind("<Configure>", self.schedule_hero_art_update, add="+")
        except (OSError, ValueError):
            self.hero_art_source = None

        link_card = RoundedPanel(shell, padding=12, fill=PANEL_SURFACE, outline="#ffffff", radius=34, surface=APP_SURFACE, shadow=True, shadow_pad=8)
        self.link_card = link_card
        link_card.pack(fill="x", pady=(0, 18))
        self.root.after_idle(self.update_hero_art)
        link_row = ttk.Frame(link_card.body, style="Card.TFrame")
        link_row.pack(fill="x")
        self.link_icon_photo = svg_icon_photo(link_row, "link-2", 24, "#63738f")
        ttk.Label(link_row, image=self.link_icon_photo, background=PANEL_SURFACE).pack(side="left", padx=(8, 14))
        self.url_entry = tk.Entry(
            link_row, bg=PANEL_SURFACE, fg="#65748e", insertbackground="#182238",
            relief="flat", bd=0, highlightthickness=0, font=(APP_FONT, 14),
        )
        self.url_entry.insert(0, self.ui_customization["placeholder"])
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(4, 8), ipady=5)
        self.url_entry.bind("<FocusIn>", self.clear_placeholder)
        self.url_entry.bind("<Return>", self.submit_entry)
        self.paste_button = RoundedButton(link_row, "Вставить", self.paste_clipboard, width=140, height=46, fill="#edf3fc", hover="#e2edfb", foreground="#425471", background=PANEL_SURFACE)
        self.paste_button.pack(side="left", padx=4)
        self.download_button = RoundedButton(link_row, self.ui_customization["download_button"], self.submit_entry, width=182, height=54, foreground="#ffffff", accent=True, background=PANEL_SURFACE)
        self.download_button.pack(side="left", padx=(4, 0))

        quality_row = ttk.Frame(shell)
        quality_row.pack(fill="x", pady=(0, 12))
        ttk.Label(quality_row, text="Качество видео", style="Muted.TLabel").pack(side="left", padx=(8, 10))
        RoundedSelect(
            quality_row,
            self.quality,
            ("720p — быстрее", "1080p — чётче", "Максимум"),
            width=190,
        ).pack(side="left")
        self.status_label = ttk.Label(shell, textvariable=self.status_detail, style="Muted.TLabel")
        # Status and progress appear through the download rows and compact
        # tray overlay; keep the large idle progress trough out of the layout.
        self.active_progress = ttk.Progressbar(shell, mode="determinate", variable=self.progress_value, maximum=100)

        tab_row = ttk.Frame(shell)
        tab_row.pack(fill="x", pady=(0, 10))
        self.tab_buttons = {}
        self.tab_button_widgets = {}
        for key, caption in (("downloads", "Загрузки"), ("history", "История"), ("settings", "Настройки")):
            glyph = {"downloads": "↓", "history": "◷", "settings": "⚙"}[key]
            button = RoundedTab(tab_row, caption, glyph, lambda k=key: self.show_tab(k), width=164)
            button.pack(side="left", padx=(0, 8))
            self.tab_buttons[key] = button
            self.tab_button_widgets[key] = button

        self.downloads_view = RoundedPanel(shell, padding=16, fill=PANEL_SURFACE, outline="#ffffff", radius=30, surface=APP_SURFACE, shadow=True, shadow_pad=10)
        list_wrap = ttk.Frame(self.downloads_view.body, style="Card.TFrame")
        list_wrap.pack(fill="both", expand=True)
        self.download_canvas = tk.Canvas(list_wrap, bg=PANEL_SURFACE, bd=0, highlightthickness=0)
        self.download_scrollbar = PloopyScrollbar(list_wrap, command=self.download_canvas.yview, background=PANEL_SURFACE)
        self.download_canvas.configure(yscrollcommand=self.download_scrollbar.set)
        self.download_scrollbar.pack(side="right", fill="y")
        self.download_canvas.pack(side="left", fill="both", expand=True)
        self.download_rows_container = tk.Frame(self.download_canvas, bg=PANEL_SURFACE, bd=0)
        self.download_rows_window = self.download_canvas.create_window((0, 0), window=self.download_rows_container, anchor="nw")
        self.download_rows_container.bind("<Configure>", lambda _e: self.download_canvas.configure(scrollregion=self.download_canvas.bbox("all")))
        self.download_canvas.bind("<Configure>", lambda e: self.download_canvas.itemconfigure(self.download_rows_window, width=e.width))
        self.empty_downloads = ttk.Label(self.download_rows_container, text="Здесь появятся загруженные файлы", background=PANEL_SURFACE, foreground="#687995", padding=24)
        self.empty_downloads.pack(anchor="center")

        self.history_view = RoundedPanel(shell, padding=16, fill=PANEL_SURFACE, outline="#ffffff", radius=30, surface=APP_SURFACE, shadow=True, shadow_pad=10)
        self.history_canvas = tk.Canvas(self.history_view.body, bg=PANEL_SURFACE, bd=0, highlightthickness=0)
        history_scroll = PloopyScrollbar(self.history_view.body, command=self.history_canvas.yview, background=PANEL_SURFACE)
        self.history_canvas.configure(yscrollcommand=history_scroll.set)
        history_scroll.pack(side="right", fill="y")
        self.history_canvas.pack(side="left", fill="both", expand=True)
        self.history_body = tk.Frame(self.history_canvas, bg=PANEL_SURFACE, bd=0)
        self.history_canvas.create_window((0, 0), window=self.history_body, anchor="nw", tags="history_body")
        self.history_body.bind("<Configure>", lambda _e: self.history_canvas.configure(scrollregion=self.history_canvas.bbox("all")))
        self.history_canvas.bind("<Configure>", lambda e: self.history_canvas.itemconfigure("history_body", width=e.width))

        self.settings_view = RoundedPanel(shell, padding=16, fill=PANEL_SURFACE, outline="#ffffff", radius=30, surface=APP_SURFACE, shadow=True, shadow_pad=10)
        self.build_settings_view()

        footer = ttk.Frame(shell)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        self.autostart_toggle = RoundedToggle(footer, "Запускать вместе с Windows", self.run_at_login_var, self.on_settings_change, width=330, background=APP_SURFACE, height=38)
        self.autostart_toggle.pack(side="left")
        FolderAction(footer, self.open_dir, background=APP_SURFACE).pack(side="right", padx=(12, 0))

        self.show_tab("downloads")
        self.populate_downloaded_files()
        self.root.bind_all("<MouseWheel>", self.scroll_downloads)
        self.root.bind_all("<Configure>", lambda _e: None)

    def _draw_window_shell(self, event=None):
        w = self.shell_canvas.winfo_width()
        h = self.shell_canvas.winfo_height()
        if w < 40 or h < 40:
            return
        pil_img = render_aa_pill(
            w,
            h,
            radius=34,
            fill=APP_SURFACE,
            outline=None,
            outline_width=0,
            pad=2,
        )
        self._shell_photo = ImageTk.PhotoImage(pil_img, master=self.shell_canvas)
        self.shell_canvas.delete("window_bg")
        self.shell_canvas.create_image(0, 0, image=self._shell_photo, anchor="nw", tags="window_bg")
        self.shell_canvas.tag_lower("window_bg")

    def _render_headline_image(self, text, size, second=None):
        scale = 2
        dpi_scale = max(1.0, self.root.winfo_fpixels("1i") / 72.0)
        font = ImageFont.truetype(str(FONT_DIR / "Manrope-variable.ttf"), max(1, round(size * dpi_scale * scale)))
        font.set_variation_by_axes([800])
        bbox = font.getbbox(text)
        pad = 6 * scale
        second_bbox = font.getbbox(second) if second is not None else None
        line_gap = 6 * scale
        line1_height = bbox[3] - bbox[1]
        width = max(
            bbox[2] - bbox[0],
            (second_bbox[2] - second_bbox[0]) if second_bbox else 0,
        ) + pad * 2
        height = line1_height + pad * 2
        if second_bbox:
            height += line_gap + second_bbox[3] - second_bbox[1]
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))

        def draw_line(line, line_bbox, y_top, gradient_prefix=False):
            origin = (pad - line_bbox[0], y_top - line_bbox[1])
            draw = ImageDraw.Draw(image)
            if gradient_prefix and line.startswith("Ploopy"):
                prefix = "Ploopy"
                prefix_bbox = font.getbbox(prefix)
                prefix_mask = Image.new("L", image.size, 0)
                ImageDraw.Draw(prefix_mask).text(origin, prefix, font=font, fill=255)
                left = max(0, origin[0] + prefix_bbox[0])
                right = min(image.width, origin[0] + prefix_bbox[2])
                gradient = Image.new("RGBA", image.size, (0, 0, 0, 0))
                gradient_draw = ImageDraw.Draw(gradient)
                for x in range(left, max(left + 1, right)):
                    t = (x - left) / max(1, right - left - 1)
                    color = (
                        round(27 * (1 - t) + 70 * t),
                        round(126 * (1 - t) + 205 * t),
                        round(246 * (1 - t) + 255 * t),
                        255,
                    )
                    gradient_draw.line((x, 0, x, image.height), fill=color)
                gradient.putalpha(prefix_mask)
                image.alpha_composite(gradient)
                suffix = line[len(prefix):]
                if suffix:
                    draw.text((origin[0] + font.getlength(prefix), origin[1]), suffix, font=font, fill="#101a31")
            else:
                draw.text(origin, line, font=font, fill="#101a31")

        draw_line(text, bbox, pad, gradient_prefix=True)
        if second_bbox:
            draw_line(second, second_bbox, pad + line1_height + line_gap)
        image = image.resize((max(1, image.width // scale), max(1, image.height // scale)), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(image, master=self.root)

    def _set_headline(self, first, second, size):
        self.headline_image = self._render_headline_image(first, size, second)
        self.heading_one_label.configure(image=self.headline_image)
        self.heading_two_label.place_forget()
        self.heading_two_y = 0

    def _draw_entry_capsule(self, event=None):
        if not hasattr(self, "entry_capsule") or not self.entry_capsule.winfo_exists():
            return
        width = self.entry_capsule.winfo_width()
        height = self.entry_capsule.winfo_height()
        if width < 100 or height < 40:
            return
        image = render_aa_gradient_pill(
            width, height, radius=42, top="#ffffff", bottom="#f5f9ff",
            outline="#ffffff", shadow=True,
        )
        self._entry_capsule_image = ImageTk.PhotoImage(image, master=self.entry_capsule)
        self.entry_capsule.itemconfigure(self._entry_capsule_item, image=self._entry_capsule_image)
        download_right = width - 14
        paste_center = download_right - 182 - 12 - 23
        text_right = paste_center - 23 - 14
        self.entry_capsule.coords(self._entry_download_window, download_right, height / 2)
        self.entry_capsule.coords(self._entry_paste_window, paste_center, height / 2)
        self.entry_capsule.coords(self._entry_text_window, 64, height / 2)
        self.entry_capsule.itemconfigure(
            self._entry_text_window, width=max(80, text_right - 64)
        )

    def update_hero_art(self):
        """Fit the mascot into the top-right hero area, allowing its bottom cloud to meet the input capsule."""
        if not hasattr(self, "hero_art_source") or not self.hero_art_label.winfo_exists():
            return
        hero_width = self.hero_area.winfo_width()
        if hero_width < 100:
            return
        config = getattr(self, "ui_customization", DEFAULT_UI_CONFIG)
        x_inset = max(0, min(240, int(config.get("hero_x", 0))))
        y_offset = max(0, min(200, int(config.get("hero_y", 0))))
        # The mascot is ~15% smaller than before. Its lower edge remains
        # anchored to the link capsule, which naturally moves the artwork up.
        max_h = max(1, 357 - y_offset)
        available_width = max(140, min(550, round(hero_width * 1.28) - x_inset))
        source = self.hero_art_source
        # Size the artwork from its real bounds. A transparent tail here made
        # the cloud reappear below the link capsule when the full layer shifted down.
        fit_width = min(
            550,
            available_width,
            round(max_h * source.width / source.height),
        )
        target_width = round(
            fit_width * max(30, min(150, int(config.get("hero_scale", 130)))) / 100
        )
        target_width = max(1, target_width)
        hero_top = self.hero_area.master.winfo_y()
        link_top = getattr(self, "link_card", None)
        link_top = link_top.winfo_y() if link_top is not None else hero_top + 300
        signature = (
            target_width, x_inset, y_offset, self.ui_customization.get("hero_image"),
            self.hero_area.master.winfo_x(), hero_top,
            self.hero_area.winfo_x(), self.hero_area.winfo_y(), hero_width, link_top,
        )
        if signature == getattr(self, "hero_art_signature", None):
            return
        art = source.copy()
        art.thumbnail((target_width, max_h), Image.Resampling.LANCZOS)
        content_height = art.height

        # The source already has a soft ambient glow. Adding a second blurred
        # halo made that glow read as a rectangular patch behind the mascot.
        self.hero_image = ImageTk.PhotoImage(art, master=self.root)
        self.hero_art_label.configure(image=self.hero_image)
        shell_x = self.hero_area.winfo_x() + hero_width - art.width - x_inset
        # Let the cloud extend a little behind the capsule. The capsule is a
        # later sibling, so it masks this overlap; the image ends inside it and
        # cannot leak out below the field.
        shell_y = link_top - content_height + 10 + y_offset - round(content_height * 0.05)
        self.hero_art_label.place(x=shell_x, y=shell_y, anchor="nw")
        if getattr(self, "link_card", None) is not None:
            card = self.link_card
        self.hero_art_width = target_width
        self.hero_art_signature = signature

    def schedule_hero_art_update(self, _event=None):
        if getattr(self, "hero_art_after_id", None) is None:
            self.hero_art_after_id = self.root.after(70, self._run_hero_art_update)

    def _run_hero_art_update(self):
        self.hero_art_after_id = None
        self.update_hero_art()

    def poll_ui_customization(self):
        """Apply edits saved by the separate Ploopy editor while the app is open."""
        stamp = self.ui_config_mtime()
        if stamp is not None and stamp != self.ui_config_stamp:
            old = self.ui_customization
            updated = load_ui_customization()
            self.apply_custom_colors(old, updated)
            self.ui_config_stamp = stamp
            self.ui_customization = updated
            self.root.title(updated["brand"])
            self.brand_label.configure(text=updated["brand"])
            h_size = int(updated["heading_size"])
            self._set_headline(updated["heading_one"], updated["heading_two"], h_size)
            self.description_label.configure(text=updated["description"], font=(APP_FONT, int(updated["description_size"])))
            self.description_label.place_configure(y=self.headline_image.height() + 10)
            if self.url_entry.get() == old.get("placeholder"):
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, updated["placeholder"])
            self.download_button.text = updated["download_button"]
            self.download_button.draw()
            try:
                image_path = Path(updated["hero_image"]).expanduser()
                art = trim_transparent_art(Image.open(image_path))
                self.hero_art_source = art
                self.hero_art_width = 0
                self.hero_art_signature = None
            except (OSError, ValueError, TypeError):
                pass
            self.update_hero_art()
            # Layout edits from the in-app editor intentionally sit on top of
            # the base appearance settings, including after an external save.
            self.root.after_idle(self.apply_saved_element_edits)
        self.ui_config_stamp = stamp
        self.root.after(900, self.poll_ui_customization)

    def apply_custom_colors(self, old, updated):
        """Recolor the existing widget tree after the editor saves a new palette."""
        global APP_SURFACE, PANEL_SURFACE
        old_bg, old_panel = old.get("background", APP_SURFACE), old.get("panel", PANEL_SURFACE)
        new_bg, new_panel = updated.get("background", old_bg), updated.get("panel", old_panel)
        APP_SURFACE, PANEL_SURFACE = new_bg, new_panel

        def visit(widget):
            try:
                bg = widget.cget("bg")
                if bg == old_bg:
                    widget.configure(bg=new_bg)
                elif bg == old_panel:
                    widget.configure(bg=new_panel)
            except (tk.TclError, TypeError):
                pass
            if isinstance(widget, RoundedPanel):
                widget.fill = new_panel
                widget.surface = new_bg
                widget.canvas.configure(bg=new_bg)
                widget.body.configure(bg=new_panel)
                widget.draw_panel()
            for child in widget.winfo_children():
                visit(child)

        visit(self.root)
        self.root.configure(bg=new_bg)

    def scroll_downloads(self, event):
        if self.downloads_view.winfo_ismapped():
            self.download_canvas.yview_scroll(int(-event.delta / 120), "units")
        elif self.history_view.winfo_ismapped():
            self.history_canvas.yview_scroll(int(-event.delta / 120), "units")

    def bind_window_drag(self, widget):
        widget.bind("<ButtonPress-1>", self.begin_window_drag)
        widget.bind("<B1-Motion>", self.move_window_drag)
        widget.bind("<ButtonRelease-1>", self.finish_window_drag)

    def begin_window_drag(self, event):
        if getattr(self, "_window_maximized", False):
            return "break"
        self._window_drag_offset = (
            event.x_root - self.root.winfo_x(),
            event.y_root - self.root.winfo_y(),
        )
        self._window_drag_pending = None
        return "break"

    def move_window_drag(self, event):
        if getattr(self, "_window_maximized", False):
            return
        dx, dy = getattr(self, "_window_drag_offset", (0, 0))
        self._window_drag_pending = (event.x_root - dx, event.y_root - dy)
        if getattr(self, "_window_drag_after_id", None) is None:
            self._window_drag_after_id = self.root.after(12, self.apply_window_drag)

    def apply_window_drag(self):
        self._window_drag_after_id = None
        position = getattr(self, "_window_drag_pending", None)
        if position is None or not self.root.winfo_exists():
            return
        self._window_drag_pending = None
        x, y = position
        if os.name == "nt":
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (ctypes.wintypes.HWND, ctypes.c_uint)
            user32.GetAncestor.restype = ctypes.wintypes.HWND
            root_hwnd = ctypes.wintypes.HWND(self.root.winfo_id())
            hwnd = user32.GetAncestor(root_hwnd, 2) or root_hwnd
            user32.SetWindowPos.argtypes = (
                ctypes.wintypes.HWND,
                ctypes.wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            )
            user32.SetWindowPos.restype = ctypes.wintypes.BOOL
            flags = 0x0001 | 0x0004 | 0x0010
            if user32.SetWindowPos(hwnd, None, x, y, 0, 0, flags):
                return
        self.root.geometry(f"+{x}+{y}")

    def finish_window_drag(self, _event=None):
        after_id = getattr(self, "_window_drag_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
            self._window_drag_after_id = None
        self.apply_window_drag()

    def toggle_maximize(self):
        if getattr(self, "_window_maximized", False):
            self.root.geometry(getattr(self, "_window_restore_geometry", "880x940"))
            self._window_maximized = False
        else:
            self._window_restore_geometry = self.root.geometry()
            self.root.geometry(
                f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0"
            )
            self._window_maximized = True

    def install_resize_handles(self, shell):
        specs = (
            ("n", {"relx": 0, "x": 16, "y": 0, "relwidth": 1, "width": -32, "height": 8, "anchor": "nw"}, "size_ns"),
            ("s", {"relx": 0, "x": 16, "rely": 1, "y": -8, "relwidth": 1, "width": -32, "height": 8, "anchor": "nw"}, "size_ns"),
            ("w", {"x": 0, "y": 16, "relheight": 1, "height": -32, "width": 8, "anchor": "nw"}, "size_we"),
            ("e", {"relx": 1, "x": -8, "y": 16, "relheight": 1, "height": -32, "width": 8, "anchor": "nw"}, "size_we"),
            ("nw", {"x": 0, "y": 0, "width": 16, "height": 16, "anchor": "nw"}, "size_nw_se"),
            ("ne", {"relx": 1, "x": -16, "y": 0, "width": 16, "height": 16, "anchor": "nw"}, "size_ne_sw"),
            ("sw", {"x": 0, "rely": 1, "y": -16, "width": 16, "height": 16, "anchor": "nw"}, "size_ne_sw"),
            ("se", {"relx": 1, "x": -16, "rely": 1, "y": -16, "width": 16, "height": 16, "anchor": "nw"}, "size_nw_se"),
        )
        for edges, geometry, cursor in specs:
            handle = tk.Frame(shell, bg=APP_SURFACE, bd=0, highlightthickness=0, cursor=cursor)
            handle.place(**geometry)
            handle.bind(
                "<ButtonPress-1>", lambda event, edge=edges: self.begin_window_resize(event, edge)
            )
            handle.bind("<B1-Motion>", self.move_window_resize)
            handle.bind("<ButtonRelease-1>", self.finish_window_resize)

    def begin_window_resize(self, event, edges="se"):
        global AA_RENDER_SCALE
        AA_RENDER_SCALE = 2
        self._resize_start = (
            event.x_root,
            event.y_root,
            self.root.winfo_x(),
            self.root.winfo_y(),
            self.root.winfo_width(),
            self.root.winfo_height(),
            edges,
        )
        return "break"

    def move_window_resize(self, event):
        if getattr(self, "_window_maximized", False):
            return
        start_x, start_y, orig_x, orig_y, orig_w, orig_h, edges = self._resize_start
        dx, dy = event.x_root - start_x, event.y_root - start_y
        width, height, x, y = orig_w, orig_h, orig_x, orig_y
        if "e" in edges:
            width = max(820, orig_w + dx)
        if "s" in edges:
            height = max(860, orig_h + dy)
        if "w" in edges:
            width = max(820, orig_w - dx)
            x = orig_x + orig_w - width
        if "n" in edges:
            height = max(860, orig_h - dy)
            y = orig_y + orig_h - height
        self._resize_pending = (x, y, width, height)
        if getattr(self, "_resize_after_id", None) is None:
            self._resize_after_id = self.root.after(20, self.apply_window_resize)

    def apply_window_resize(self):
        self._resize_after_id = None
        geometry = getattr(self, "_resize_pending", None)
        if geometry is None or not self.root.winfo_exists():
            return
        self._resize_pending = None
        x, y, width, height = geometry
        if os.name == "nt":
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (ctypes.wintypes.HWND, ctypes.c_uint)
            user32.GetAncestor.restype = ctypes.wintypes.HWND
            user32.SetWindowPos.argtypes = (
                ctypes.wintypes.HWND,
                ctypes.wintypes.HWND,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_uint,
            )
            user32.SetWindowPos.restype = ctypes.wintypes.BOOL
            root_hwnd = ctypes.wintypes.HWND(self.root.winfo_id())
            hwnd = user32.GetAncestor(root_hwnd, 2) or root_hwnd
            if hwnd:
                flags = 0x0010
                if user32.SetWindowPos(hwnd, None, x, y, width, height, flags):
                    return
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def finish_window_resize(self, _event=None):
        global AA_RENDER_SCALE
        after_id = getattr(self, "_resize_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
            self._resize_after_id = None
        self.apply_window_resize()
        AA_RENDER_SCALE = 4
        if self.root.winfo_exists():
            self.root.after(50, self.refresh_aa_shapes)

    def refresh_aa_shapes(self):
        """Redraw rounded widgets at full quality after interactive resizing."""
        if not self.root.winfo_exists():
            return

        def visit(widget):
            if isinstance(widget, RoundedPanel):
                widget._last_panel_size = (widget.winfo_width(), widget.winfo_height())
                widget.draw_panel()
            elif isinstance(
                widget,
                (RoundedButton, RoundedTab, RoundedSelect, RoundedProgress,
                 CircleActionButton, WindowControl, RoundedToggle),
            ):
                widget.draw()
            for child in widget.winfo_children():
                visit(child)

        self._draw_window_shell()
        visit(self.root)

    def clear_placeholder(self, _event=None):
        if self.url_entry.get() == self.ui_customization.get("placeholder", "Вставь ссылку..."):
            self.url_entry.delete(0, "end")
            self.url_entry.configure(foreground="#182238")

    def build_settings_view(self):
        body = self.settings_view.body
        tk.Label(
            body,
            text="Настройки приложения",
            bg=PANEL_SURFACE,
            fg="#182540",
            font=(APP_FONT, 18, "bold"),
        ).pack(anchor="w", pady=(0, 10))
        # Keep the browser selector near the top: the settings card has a fixed
        # height, so controls placed after the folder row can be clipped.
        browser_row = tk.Frame(body, bg=PANEL_SURFACE)
        browser_row.pack(fill="x", pady=(0, 8))
        tk.Label(
            browser_row,
            text="Вход через браузер",
            bg=PANEL_SURFACE,
            fg="#5f6f89",
            font=(APP_FONT, 12),
        ).pack(side="left", padx=(0, 10))
        RoundedSelect(
            browser_row,
            self.browser,
            ("Автоматически", "Без авторизации", "Chrome", "Edge", "Firefox", "Brave", "Opera"),
            width=175,
        ).pack(side="left")
        hotkey_row = tk.Frame(body, bg=PANEL_SURFACE)
        hotkey_row.pack(fill="x", pady=(0, 7))
        tk.Label(
            hotkey_row, text="Скачать по сочетанию", bg=PANEL_SURFACE,
            fg="#5f6f89", font=(APP_FONT, 12),
        ).pack(side="left", padx=(0, 10))
        self.hotkey_entry = tk.Entry(
            hotkey_row, textvariable=self.download_hotkey_var, width=20,
            justify="center", relief="flat", bd=0, highlightthickness=1,
            highlightbackground="#dce7f6", highlightcolor="#5a9af5",
            bg="#edf4ff", fg="#273650", font=(APP_FONT, 11),
        )
        self.hotkey_entry.pack(side="left", ipady=6)
        self.hotkey_entry.bind("<KeyPress>", self.capture_download_hotkey)
        tk.Label(
            hotkey_row, text="Нажми поле и введи сочетание", bg=PANEL_SURFACE,
            fg="#8996aa", font=(APP_FONT, 9),
        ).pack(side="left", padx=9)
        for label, variable in (
            ("Запускать вместе с Windows", self.run_at_login_var),
            ("При запуске сворачивать в трей", self.start_minimized_var),
            ("Звуковой сигнал после загрузки", self.sound_var),
        ):
            RoundedToggle(
                body,
                label,
                variable,
                self.on_settings_change,
                width=380,
                background=PANEL_SURFACE,
                height=34,
            ).pack(anchor="w", pady=3)
        folder = tk.Frame(body, bg=PANEL_SURFACE)
        folder.pack(fill="x", pady=(10, 6))
        tk.Label(
            folder, text="Папка загрузок", bg=PANEL_SURFACE, fg="#5f6f89", font=(APP_FONT, 12)
        ).pack(anchor="w", pady=(0, 5))
        folder_field = RoundedPanel(
            folder,
            padding=0,
            fill="#edf4ff",
            outline="#e1ecfb",
            radius=14,
            surface=PANEL_SURFACE,
            shadow=False,
        )
        folder_field.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.folder_entry = tk.Entry(
            folder_field.body,
            textvariable=self.download_dir,
            bg="#edf4ff",
            fg="#273650",
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=(APP_FONT, 12),
        )
        self.folder_entry.pack(fill="x", ipady=8, padx=12, pady=2)
        RoundedButton(
            folder, "Выбрать папку", self.choose_dir, width=140, height=40, background=PANEL_SURFACE
        ).pack(side="left")
    def show_tab(self, key):
        for view in (self.downloads_view, self.history_view, self.settings_view):
            view.pack_forget()
        view = {
            "downloads": self.downloads_view,
            "history": self.history_view,
            "settings": self.settings_view,
        }[key]
        view.pack(fill="both", expand=True)
        for tab, button in self.tab_buttons.items():
            button.set_selected(tab == key)
        if key == "history":
            self.render_history()

    def create_download_row(self, url, title=None, detail="В очереди", icon="▶"):
        if hasattr(self, "empty_downloads") and self.empty_downloads.winfo_exists():
            self.empty_downloads.pack_forget()

        frame = tk.Frame(self.download_rows_container, bg=PANEL_SURFACE, height=96)
        frame.pack(fill="x", pady=0)
        frame.pack_propagate(False)

        # Left custom badge icon matching the concept (Video / Photo / Archive)
        badge_kind = (
            "video"
            if icon in {"▶", "video"}
            else "image"
            if icon in {"▧", "image"}
            else "archive"
        )
        badge_photo = make_file_badge_photo(frame, badge_kind)
        icon_label = tk.Label(frame, image=badge_photo, bg=PANEL_SURFACE, bd=0)
        icon_label._photo = badge_photo
        icon_label.pack(side="left", padx=(4, 16), pady=14)

        # Right side controls container (Pause/Cancel or Green Check)
        controls = tk.Frame(frame, bg=PANEL_SURFACE)
        controls.pack(side="right", padx=(12, 4), pady=18)

        pause_btn = CircleActionButton(
            controls,
            "pause",
            command=lambda u=url: self.toggle_pause(u),
            fill="#eef3fb",
            hover="#dfe9f8",
            fg="#3b4a66",
            size=36,
        )
        pause_btn.pack(side="left", padx=4)

        cancel_btn = CircleActionButton(
            controls,
            "x",
            command=lambda u=url: self.cancel_download(u),
            fill="#eef3fb",
            hover="#ffe6ea",
            fg="#3b4a66",
            size=36,
        )
        cancel_btn.pack(side="left", padx=4)

        success_badge = CircleActionButton(
            controls,
            "check",
            command=None,
            fill="#dcf6e8",
            hover="#dcf6e8",
            fg="#21a66a",
            size=34,
        )
        success_badge.pack_forget()

        failure_badge = CircleActionButton(
            controls,
            "x",
            command=None,
            fill="#ffe4e8",
            hover="#ffe4e8",
            fg="#d85a6a",
            size=34,
        )
        failure_badge.pack_forget()

        # Center content: top line = filename, bottom line = detail + progress bar + percentage
        center = tk.Frame(frame, bg=PANEL_SURFACE)
        center.pack(side="left", fill="both", expand=True, pady=9)

        full_title = title or self.title_from_url(url)
        title_label = tk.Label(
            center,
            text=full_title,
            bg=PANEL_SURFACE,
            fg="#141e33",
            font=(APP_FONT, 13, "bold"),
            anchor="w",
            justify="left",
        )
        title_label.pack(anchor="w", fill="x")
        center.bind(
            "<Configure>",
            lambda event, label=title_label: label.configure(
                wraplength=max(240, event.width - 8)
            ),
        )
        title_label.bind(
            "<Enter>", lambda e, u=url: self.show_file_tooltip(e, self.download_rows[u]["full_title"])
        )
        title_label.bind("<Leave>", lambda _e: self.hide_file_tooltip())

        bottom_line = tk.Frame(center, bg=PANEL_SURFACE)
        bottom_line.pack(fill="x", pady=(4, 0))

        detail_label = tk.Label(
            bottom_line,
            text=detail,
            bg=PANEL_SURFACE,
            fg="#67758e",
            font=(APP_FONT, 11),
            anchor="w",
        )
        detail_label.pack(side="left")
        detail_label.bind(
            "<Enter>",
            lambda e, u=url: self.show_file_tooltip(
                e, self.download_rows[u]["detail"].cget("text")
            ),
        )
        detail_label.bind("<Leave>", lambda _e: self.hide_file_tooltip())

        percent_label = tk.Label(
            bottom_line,
            text="",
            bg=PANEL_SURFACE,
            fg="#33435e",
            font=(APP_FONT, 11, "bold"),
        )
        percent_label.pack(side="right", padx=(10, 4))

        progress = RoundedProgress(bottom_line, height=9)
        progress.configure(bg=PANEL_SURFACE)
        progress.pack(side="left", fill="x", expand=True, padx=(22, 6), pady=4)

        divider = tk.Frame(self.download_rows_container, bg="#ebf1fa", height=1)
        divider.pack(fill="x", padx=4)

        self.download_rows[url] = {
            "frame": frame,
            "title": title_label,
            "full_title": full_title,
            "detail": detail_label,
            "progress": progress,
            "percent": percent_label,
            "pause": pause_btn,
            "cancel": cancel_btn,
            "success": success_badge,
            "failure": failure_badge,
            "divider": divider,
        }
        if getattr(self, "visual_edit_mode", False):
            self._set_edit_bindtags(frame, True)
        if getattr(self, "element_edits", None):
            self.root.after_idle(lambda row=frame: self.apply_saved_element_edits(row))

    def show_file_tooltip(self, event, text):
        self.hide_file_tooltip()
        self._file_tooltip = tk.Toplevel(self.root)
        self._file_tooltip.overrideredirect(True)
        self._file_tooltip.attributes("-topmost", True)
        label = tk.Label(
            self._file_tooltip,
            text=text,
            bg="#ffffff",
            fg="#263650",
            font=(APP_FONT, 10),
            padx=10,
            pady=6,
            relief="solid",
            bd=1,
        )
        label.pack()
        self._file_tooltip.geometry(f"+{event.x_root + 12}+{event.y_root + 18}")

    def hide_file_tooltip(self):
        tooltip = getattr(self, "_file_tooltip", None)
        if tooltip and tooltip.winfo_exists():
            tooltip.destroy()
        self._file_tooltip = None

    def populate_downloaded_files(self):
        folder = Path(self.download_dir.get()).expanduser()
        try:
            files = sorted(
                (p for p in folder.iterdir() if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        if not files:
            return
        self.empty_downloads.pack_forget()
        for path in files[:10]:
            try:
                info = path.stat()
            except OSError:
                continue
            suffix = path.suffix.lower()
            icon = (
                "▶"
                if suffix in {".mp4", ".mov", ".mkv", ".webm"}
                else "▧"
                if suffix in {".jpg", ".png", ".webp"}
                else "▱"
            )
            key = f"local:{path}"
            size = self.format_size(info.st_size)
            self.create_download_row(key, path.name, f"{size}  ·  Готово", icon)
            self.update_download_row(key, progress=100, finished=True)
            if not any(item.get("title") == path.name for item in self.history):
                self.history.append(
                    {
                        "title": path.name,
                        "size": size,
                        "date": time.strftime("%d.%m.%Y %H:%M", time.localtime(info.st_mtime)),
                    }
                )
        self.history = self.history[-200:]
        self.save_history()
        # Populated rows may change the scroll region after the first layout.
        self.root.after_idle(lambda: self.download_canvas.yview_moveto(0))

    @staticmethod
    def format_size(size):
        amount = float(size)
        for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
            if amount < 1024 or unit == "ТБ":
                return (
                    f"{amount:.0f} {unit}"
                    if unit in ("Б", "КБ")
                    else f"{amount:.1f} {unit}".replace(".", ",")
                )
            amount /= 1024

    @staticmethod
    def title_from_url(url):
        parsed = urllib.parse.urlparse(url)
        name = Path(urllib.parse.unquote(parsed.path)).name
        return name or parsed.hostname or "Новая загрузка"

    def update_download_row(
        self, url, *, title=None, detail=None, progress=None, size=None, finished=False
    ):
        row = self.download_rows.get(url)
        if not row:
            return
        if title:
            row["full_title"] = title
            override = self.element_edits.get("texts", {}).get(row["title"]._w)
            row["title"].configure(text=override if override is not None else title)
        if detail is not None:
            state_color = (
                "#d75a69"
                if any(term in detail.lower() for term in ("ошибка", "error", "не удалось"))
                else "#67758e"
            )
            if "приостанов" in detail.lower():
                state_color = "#b07a25"
            row["detail"].configure(text=detail, fg=state_color)
        if progress is not None:
            row["progress"].set(progress)
            row["percent"].configure(text=f"{float(progress):.0f}%" if progress < 100 else "")
        if size is not None:
            row["size"] = size
        if finished:
            row["pause"].pack_forget()
            row["cancel"].pack_forget()
            row["progress"].pack_forget()
            row["percent"].pack_forget()
            if "Готово" in row["detail"].cget("text"):
                row["success"].pack(side="right", padx=4)
            else:
                row["failure"].pack(side="right", padx=4)

    def render_history(self):
        for child in self.history_body.winfo_children():
            child.destroy()
        if not self.history:
            tk.Label(
                self.history_body,
                text="Здесь появятся завершённые загрузки",
                bg=PANEL_SURFACE,
                fg="#7a879e",
                font=(APP_FONT, 14),
            ).pack(expand=True, pady=40)
            if self.visual_edit_mode:
                self._set_edit_bindtags(self.history_body, True)
            self.root.after_idle(lambda: self.apply_saved_element_edits(self.history_body))
            return
        for item in reversed(self.history):
            row = tk.Frame(self.history_body, bg=PANEL_SURFACE, height=76)
            row.pack(fill="x", pady=0)
            row.pack_propagate(False)
            badge = CircleActionButton(
                row,
                "check",
                command=None,
                fill="#dcf6e8",
                hover="#dcf6e8",
                fg="#21a66a",
                size=40,
            )
            badge.pack(side="left", padx=(6, 14), pady=16)
            info = tk.Frame(row, bg=PANEL_SURFACE)
            info.pack(side="left", fill="both", expand=True, pady=14)
            title_label = tk.Label(
                info,
                text=item.get("title", "Файл"),
                bg=PANEL_SURFACE,
                fg="#162138",
                font=(APP_FONT, 13, "bold"),
                anchor="w",
                justify="left",
            )
            title_label.pack(anchor="w", fill="x")
            tk.Label(
                info,
                text=f"{item.get('size', 'Файл сохранён')}  ·  {item.get('date', '')}",
                bg=PANEL_SURFACE,
                fg="#67758e",
                font=(APP_FONT, 11),
                anchor="w",
            ).pack(anchor="w", pady=(3, 0))
            tk.Frame(self.history_body, bg="#ebf1fa", height=1).pack(fill="x", padx=4)
        if self.visual_edit_mode:
            self._set_edit_bindtags(self.history_body, True)
        self.root.after_idle(lambda: self.apply_saved_element_edits(self.history_body))

    def on_settings_change(self):
        self.settings.update(
            {
                "start_minimized": self.start_minimized_var.get(),
                "run_at_login": self.run_at_login_var.get(),
                "sound": self.sound_var.get(),
                "download_hotkey": self.download_hotkey_var.get(),
            }
        )
        self.save_settings()
        self.set_autostart(self.settings["run_at_login"])
        self.update_tray_hotkey()
        self.refresh_download_hotkey()

    def capture_download_hotkey(self, event):
        key = event.keysym
        if key in ("Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R", "Super_L", "Super_R"):
            return "break"
        modifiers = []
        if event.state & 0x0004:
            modifiers.append("Ctrl")
        if event.state & 0x0008:
            modifiers.append("Alt")
        if event.state & 0x0001:
            modifiers.append("Shift")
        if event.state & 0x0040:
            modifiers.append("Win")
        if not modifiers:
            self.status_detail.set("Для глобального сочетания добавь Ctrl, Alt, Shift или Win")
            return "break"
        key_name = {"Escape": "Esc", "Return": "Enter", "Prior": "PageUp", "Next": "PageDown"}.get(key, key)
        candidate = " + ".join(modifiers + [key_name])
        try:
            _mods, _vk, normalized = parse_hotkey(candidate)
        except ValueError:
            self.status_detail.set("Эта клавиша не подходит для сочетания")
            return "break"
        self.download_hotkey_var.set(normalized)
        self.on_settings_change()
        return "break"

    def update_tray_hotkey(self):
        global HOTKEY_TEXT
        HOTKEY_TEXT = self.settings.get("download_hotkey", DEFAULT_HOTKEY)
        if self.tray_icon:
            self.tray_icon.title = f"{self.ui_customization['brand']} · {HOTKEY_TEXT}"
            self.tray_icon.menu = self.build_tray_menu()
            self.tray_icon.update_menu()

    @staticmethod
    def load_history():
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError, TypeError):
            return []

    def save_history(self):
        HISTORY_PATH.write_text(
            json.dumps(self.history[-200:], ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def add_history_item(self, title, size="Готово"):
        self.history.append(
            {"title": title or "Файл", "size": size, "date": time.strftime("%d.%m.%Y %H:%M")}
        )
        self.history = self.history[-200:]
        self.save_history()

    def set_progress(self, value):
        self.progress_value.set(value)
        if hasattr(self, "progress") and self.progress.winfo_exists():
            self.progress.set(value)

    def set_autostart(self, enabled: bool):
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE
            ) as key:
                value_name = "LinkDownloader"
                if enabled:
                    pythonw = Path(sys.executable) if getattr(sys, "frozen", False) else Path(sys.executable).with_name("pythonw.exe")
                    tray_arg = " --tray" if self.start_minimized_var.get() else ""
                    command = f'"{pythonw}"' if getattr(sys, "frozen", False) else f'"{pythonw}" "{Path(__file__).resolve()}"{tray_arg}'
                    winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command)
                else:
                    try:
                        winreg.DeleteValue(key, value_name)
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            self.status_detail.set(f"Не удалось изменить автозапуск: {exc}")

    def make_tray_image(self):
        for candidate in (APP_DIR / "Ploopy.png", APP_DIR / "Ploopy.ico"):
            try:
                if candidate.is_file():
                    return (
                        Image.open(candidate)
                        .convert("RGBA")
                        .resize((64, 64), Image.Resampling.LANCZOS)
                    )
            except OSError:
                continue
        return render_aa_pill(64, 64, radius=18, fill="#2581f6")

    def build_tray_menu(self):
        return pystray.Menu(
            pystray.MenuItem(
                "Открыть Загрузчик",
                lambda _icon, _item: self.root.after(0, self.show_window),
                default=True,
            ),
            pystray.MenuItem(
                f"Скачать ссылку из буфера  ({HOTKEY_TEXT})",
                lambda _icon, _item: self.root.after(0, self.download_clipboard),
            ),
            pystray.MenuItem(
                "Проверить обновления",
                lambda _icon, _item: self.root.after(0, lambda: self.check_for_updates(manual=True)),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda _icon, _item: self.root.after(0, self.quit_app)),
        )

    def start_tray(self):
        menu = self.build_tray_menu()
        self.tray_icon = pystray.Icon(
            "ploopy", self.make_tray_image(),
            f"{self.ui_customization['brand']} · {self.settings.get('download_hotkey', DEFAULT_HOTKEY)}", menu
        )

        def setup(icon):
            icon.visible = True
            self.tray_ready.set()

        self.tray_icon.run_detached(setup)
        if self.start_hidden_requested:
            threading.Thread(target=self.wait_for_tray, daemon=True).start()

    def wait_for_tray(self):
        if self.tray_ready.wait(8):
            self.root.after(0, self.hide_to_tray)
        else:
            self.root.after(
                0,
                lambda: self.status_detail.set(
                    "Windows не добавила значок в трей — окно оставлено открытым"
                ),
            )

    def start_global_hotkey(self):
        def listen():
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.RegisterHotKey.argtypes = (
                ctypes.wintypes.HWND,
                ctypes.c_int,
                ctypes.wintypes.UINT,
                ctypes.wintypes.UINT,
            )
            user32.RegisterHotKey.restype = ctypes.wintypes.BOOL
            user32.UnregisterHotKey.argtypes = (ctypes.wintypes.HWND, ctypes.c_int)
            user32.UnregisterHotKey.restype = ctypes.wintypes.BOOL
            user32.GetMessageW.argtypes = (
                ctypes.POINTER(ctypes.wintypes.MSG),
                ctypes.wintypes.HWND,
                ctypes.wintypes.UINT,
                ctypes.wintypes.UINT,
            )
            user32.GetMessageW.restype = ctypes.c_int
            user32.PostThreadMessageW.argtypes = (ctypes.wintypes.DWORD, ctypes.wintypes.UINT, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
            user32.PostThreadMessageW.restype = ctypes.wintypes.BOOL
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GetCurrentThreadId.restype = ctypes.wintypes.DWORD
            self.hotkey_thread_id = kernel32.GetCurrentThreadId()
            # Create this thread's message queue before the UI can request a re-registration.
            msg = ctypes.wintypes.MSG()
            user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
            download_hotkey_id = 0x4C44
            editor_hotkey_id = 0x4C45
            configure_message = 0x8000 + 44
            registered = set()
            current = None

            def register_download():
                nonlocal current
                if download_hotkey_id in registered:
                    user32.UnregisterHotKey(None, download_hotkey_id)
                    registered.discard(download_hotkey_id)
                try:
                    modifiers, virtual_key, shown = parse_hotkey(self.settings.get("download_hotkey", DEFAULT_HOTKEY))
                    if user32.RegisterHotKey(None, download_hotkey_id, modifiers | 0x4000, virtual_key):
                        registered.add(download_hotkey_id)
                        current = (modifiers, virtual_key)
                        self.root.after(0, lambda: self.hotkey_registration_succeeded(shown))
                    else:
                        error = ctypes.get_last_error()
                        current = None
                        self.root.after(0, lambda error=error: self.hotkey_registration_failed(error))
                except ValueError:
                    current = None

            # Register independently: a conflict with one shortcut must not
            # disable the other one.
            register_download()
            for hotkey_id, virtual_key in ((editor_hotkey_id, 0x45),):
                modifiers = 0x0002 | 0x0004
                if user32.RegisterHotKey(None, hotkey_id, modifiers, virtual_key):
                    registered.add(hotkey_id)
                else:
                    error = ctypes.get_last_error()
                    if hotkey_id == download_hotkey_id:
                        self.root.after(
                            0, lambda error=error: self.hotkey_registration_failed(error)
                        )
                    else:
                        self.root.after(
                            0,
                            lambda error=error: self.status_detail.set(
                                "Не удалось включить Ctrl + Shift + E: "
                                f"сочетание занято (ошибка Windows {error})."
                            ),
                        )
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == configure_message:
                    register_download()
                    continue
                if msg.message != 0x0312:
                    continue
                if msg.wParam == download_hotkey_id:
                    self.root.after(0, self.download_clipboard)
                elif msg.wParam == editor_hotkey_id:
                    self.root.after(0, self.activate_visual_edit_mode)
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)

        self.hotkey_thread = threading.Thread(target=listen, daemon=True)
        self.hotkey_thread.start()

    def hotkey_registration_succeeded(self, shown):
        self.status_detail.set(f"Сочетание для скачивания: {shown}")

    def refresh_download_hotkey(self):
        thread_id = getattr(self, "hotkey_thread_id", None)
        if thread_id:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            post_message = user32.PostThreadMessageW
            post_message.argtypes = (
                ctypes.wintypes.DWORD,
                ctypes.wintypes.UINT,
                ctypes.wintypes.WPARAM,
                ctypes.wintypes.LPARAM,
            )
            post_message.restype = ctypes.wintypes.BOOL
            if not post_message(thread_id, 0x8000 + 44, 0, 0):
                self.root.after(100, self.refresh_download_hotkey)

    @staticmethod
    def version_tuple(value):
        try:
            return tuple(int(part) for part in value.strip().lstrip("vV").split("."))
        except (AttributeError, ValueError):
            return ()

    def check_for_updates(self, manual=False):
        if self.update_check_in_progress:
            return
        self.update_check_in_progress = True
        threading.Thread(
            target=self._check_for_updates_worker,
            args=(manual,),
            daemon=True,
            name="ploopy-update-check",
        ).start()

    def _check_for_updates_worker(self, manual):
        try:
            request = urllib.request.Request(
                f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest",
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "Ploopy-Updater",
                },
            )
            with urllib.request.urlopen(request, timeout=12) as response:
                release = json.loads(response.read().decode("utf-8"))
            latest = release.get("tag_name", "").lstrip("vV")
            if not self.version_tuple(latest):
                raise ValueError("У GitHub указан некорректный номер версии")
            asset = next(
                (item for item in release.get("assets", []) if item.get("name") == f"Ploopy-v{latest}.zip"),
                None,
            )
            if not asset:
                raise ValueError("В опубликованном релизе не найден установочный архив")
            if self.version_tuple(latest) > self.version_tuple(APP_VERSION):
                self.root.after(0, lambda: self.offer_update(release, asset))
            elif manual:
                self.root.after(0, lambda: messagebox.showinfo("Обновления Ploopy", f"Установлена актуальная версия {APP_VERSION}.", parent=self.root))
        except Exception as exc:
            if manual:
                self.root.after(0, lambda exc=exc: messagebox.showerror("Обновления Ploopy", f"Не удалось проверить обновления: {exc}", parent=self.root))
        finally:
            self.update_check_in_progress = False

    def offer_update(self, release, asset):
        latest = release.get("tag_name", "").lstrip("vV")
        if not messagebox.askyesno(
            "Доступно обновление Ploopy",
            f"Выпущена версия {latest}. Установить её сейчас? Приложение закроется и откроется снова после обновления.",
            parent=self.root,
        ):
            return
        threading.Thread(
            target=self._download_and_apply_update,
            args=(latest, asset),
            daemon=True,
            name="ploopy-update-download",
        ).start()

    def _download_and_apply_update(self, latest, asset):
        archive = None
        try:
            download_url = asset.get("browser_download_url", "")
            parsed = urllib.parse.urlparse(download_url)
            if parsed.scheme != "https" or parsed.hostname != "github.com" or f"/{GITHUB_REPOSITORY}/releases/download/" not in parsed.path:
                raise ValueError("Ссылка на архив обновления не прошла проверку")
            archive = Path(tempfile.gettempdir()) / f"Ploopy-{latest}-{os.getpid()}.zip"
            digest = hashlib.sha256()
            request = urllib.request.Request(download_url, headers={"User-Agent": "Ploopy-Updater"})
            with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
            expected_digest = asset.get("digest", "")
            if expected_digest != f"sha256:{digest.hexdigest()}":
                raise ValueError("Контрольная сумма архива не совпала")
            updater = APP_DIR / "Apply-Update.ps1"
            if not updater.is_file():
                raise FileNotFoundError("Не найден помощник установки обновления")
            command = [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(updater), "-Archive", str(archive),
                "-InstallDir", str(APP_DIR), "-ProcessId", str(os.getpid()),
            ]
            subprocess.Popen(
                command,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
            self.root.after(0, self.quit_app)
        except Exception as exc:
            self.root.after(0, lambda exc=exc: messagebox.showerror("Обновления Ploopy", f"Не удалось установить обновление: {exc}", parent=self.root))

    def hotkey_registration_failed(self, error):
        message = f"Не удалось включить {HOTKEY_TEXT}: сочетание занято (ошибка Windows {error})."
        self.status_detail.set(message)

    def show_window(self):
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def hide_to_tray(self):
        self.root.withdraw()

    def quit_app(self):
        if self.tray_icon:
            self.tray_icon.stop()
        self.hide_overlay()
        self.release_private_fonts()
        self.root.destroy()

    def release_private_fonts(self):
        if not self.private_font_paths or os.name != "nt":
            return
        try:
            remove_font = ctypes.windll.gdi32.RemoveFontResourceExW
            remove_font.argtypes = (
                ctypes.wintypes.LPCWSTR,
                ctypes.wintypes.DWORD,
                ctypes.c_void_p,
            )
            remove_font.restype = ctypes.wintypes.BOOL
            for path in self.private_font_paths:
                remove_font(str(path), PRIVATE_FONT_FLAG, None)
        except (AttributeError, OSError):
            pass
        self.private_font_paths.clear()

    def show_download_overlay(
        self, title="Идёт скачивание", detail="", percent=None, done=False
    ):
        created = self.overlay is None or not self.overlay.winfo_exists()
        if created:
            self.overlay = tk.Toplevel(self.root)
            self.overlay.withdraw()
            self.overlay.overrideredirect(True)
            self.overlay.attributes("-topmost", True)
            self.overlay.configure(bg="#f3f7fc")
            self.overlay_canvas = None
            self.overlay.bind("<Button-1>", lambda _event: self.show_window())
            self.overlay.bind("<Button-3>", lambda _event: self.show_window())
            self.overlay_frame_index = 0
            now = time.monotonic()
            self.overlay_next_frame_at = (
                now + self.overlay_animation_frames[0][1] / 1000
                if self.overlay_animation_frames
                else 0
            )
            self.overlay_last_draw_at = 0.0
            self.overlay_generation += 1

        if percent is not None:
            self.overlay_percent = max(0.0, min(100.0, float(percent)))
        elif created:
            self.overlay_percent = 0.0
        self.overlay_done = done
        # Completion is handled by closing the compact progress window. Do not
        # turn it into a second cloud/message notification.
        if not done:
            self.cancel_overlay_timer("overlay_hide_after_id")
        if done:
            self.cancel_overlay_timer("overlay_after_id")
        if created:
            width, height = 260, 300
            left, top = self.taskbar_work_area_bottom_right()
            x = left - width - 14
            y = top - height - 12
            self.overlay.geometry(f"{width}x{height}+{x}+{y}")
            self.overlay.update_idletasks()
            self.overlay.deiconify()
            self.overlay.update_idletasks()
            self.enable_layered_overlay()
            self.overlay.lift()
        self.draw_download_overlay(force=created)
        if done:
            generation = self.overlay_generation
            self.overlay_hide_after_id = self.root.after(
                4000, lambda current=generation: self.hide_overlay(current)
            )
        elif self.overlay_after_id is None:
            self.overlay_after_id = self.root.after(60, self.animate_download_overlay)

    def load_overlay_animation(self):
        if not DOWNLOAD_ANIMATION_PATH.is_file():
            return []
        frames = []
        with Image.open(DOWNLOAD_ANIMATION_PATH) as animation:
            for index in range(getattr(animation, "n_frames", 1)):
                animation.seek(index)
                duration = max(16, int(animation.info.get("duration", 100)))
                frame = self.remove_legacy_magenta(animation.convert("RGBA"))
                frame = (
                    frame.convert("RGBa")
                    .resize((180, 180), Image.Resampling.LANCZOS)
                    .convert("RGBA")
                )
                frame.load()
                frames.append((frame, duration))
        return frames

    def preload_overlay_animation(self):
        try:
            frames = self.load_overlay_animation()
        except (OSError, tk.TclError):
            frames = []
        self.overlay_animation_frames = frames
        self.overlay_animation_loaded = True

    @staticmethod
    def remove_legacy_magenta(image):
        red, green, blue, alpha = image.convert("RGBA").split()
        red_key = red.point(lambda value: 255 if value >= 180 else 0)
        green_key = green.point(lambda value: 255 if value <= 100 else 0)
        blue_key = blue.point(lambda value: 255 if value >= 180 else 0)
        key = ImageChops.darker(ImageChops.darker(red_key, green_key), blue_key)
        if key.getbbox() is None:
            return image

        alpha = ImageChops.subtract(alpha, key)
        rgb = Image.merge("RGB", (red, green, blue))
        clean_rgb = Image.composite(Image.new("RGB", image.size, (0, 0, 0)), rgb, key)
        return Image.merge("RGBA", (*clean_rgb.split(), alpha))

    def taskbar_work_area_bottom_right(self):
        try:
            rect = ctypes.wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.right, rect.bottom
        except (AttributeError, OSError):
            pass
        return self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def enable_layered_overlay(self):
        if os.name != "nt" or not self.overlay or not self.overlay.winfo_exists():
            return
        user32 = ctypes.windll.user32
        hwnd = user32.GetAncestor(self.overlay.winfo_id(), 2) or self.overlay.winfo_id()
        self.overlay_hwnd = hwnd
        get_style = user32.GetWindowLongPtrW
        set_style = user32.SetWindowLongPtrW
        get_style.argtypes = (ctypes.wintypes.HWND, ctypes.c_int)
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = (ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        set_style.restype = ctypes.c_ssize_t
        ex_style = get_style(hwnd, -20)
        layered = 0x00080000
        tool_window = 0x00000080
        if ex_style & layered:
            set_style(hwnd, -20, ex_style & ~layered)
        set_style(hwnd, -20, (ex_style & ~layered) | layered | tool_window)

    def update_layered_overlay(self, rgba_image):
        if os.name != "nt" or not self.overlay_hwnd:
            return False
        width, height = rgba_image.size
        rgba = rgba_image.convert("RGBA")
        red, green, blue, alpha = rgba.split()
        red = ImageChops.multiply(red, alpha)
        green = ImageChops.multiply(green, alpha)
        blue = ImageChops.multiply(blue, alpha)
        bgra = Image.merge("RGBA", (blue, green, red, alpha)).tobytes()

        gdi, user32 = ctypes.windll.gdi32, ctypes.windll.user32
        if not getattr(self, "_overlay_winapi_configured", False):
            gdi.CreateCompatibleDC.argtypes = (ctypes.c_void_p,)
            gdi.CreateCompatibleDC.restype = ctypes.c_void_p
            gdi.CreateDIBSection.argtypes = (
                ctypes.c_void_p,
                ctypes.POINTER(_BitmapInfo),
                ctypes.c_uint,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.c_void_p,
                ctypes.c_uint32,
            )
            gdi.CreateDIBSection.restype = ctypes.c_void_p
            gdi.SelectObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
            gdi.SelectObject.restype = ctypes.c_void_p
            gdi.DeleteObject.argtypes = (ctypes.c_void_p,)
            gdi.DeleteObject.restype = ctypes.wintypes.BOOL
            gdi.DeleteDC.argtypes = (ctypes.c_void_p,)
            gdi.DeleteDC.restype = ctypes.wintypes.BOOL
            gdi.GdiFlush.restype = ctypes.wintypes.BOOL
            user32.GetDC.argtypes = (ctypes.c_void_p,)
            user32.GetDC.restype = ctypes.c_void_p
            user32.ReleaseDC.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
            user32.ReleaseDC.restype = ctypes.c_int
            user32.UpdateLayeredWindow.argtypes = (
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.POINTER(_WinPoint),
                ctypes.POINTER(_WinSize),
                ctypes.c_void_p,
                ctypes.POINTER(_WinPoint),
                ctypes.c_uint32,
                ctypes.POINTER(_WinBlend),
                ctypes.c_uint32,
            )
            user32.UpdateLayeredWindow.restype = ctypes.wintypes.BOOL
            self._overlay_winapi_configured = True

        if (
            self.overlay_dib_size != (width, height)
            or not self.overlay_dib_dc
            or not self.overlay_dib_bits
        ):
            self.release_overlay_dib()
            screen_dc = user32.GetDC(None)
            memory_dc = gdi.CreateCompatibleDC(screen_dc)
            info = _BitmapInfo()
            info.bmiHeader.biSize = ctypes.sizeof(_BitmapInfoHeader)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0
            bits = ctypes.c_void_p()
            bitmap = gdi.CreateDIBSection(
                memory_dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0
            )
            if not bitmap or not bits.value:
                if memory_dc:
                    gdi.DeleteDC(memory_dc)
                if screen_dc:
                    user32.ReleaseDC(None, screen_dc)
                return False
            previous = gdi.SelectObject(memory_dc, bitmap)
            user32.ReleaseDC(None, screen_dc)
            self.overlay_dib_dc = memory_dc
            self.overlay_dib_bitmap = bitmap
            self.overlay_dib_previous = previous
            self.overlay_dib_bits = bits.value
            self.overlay_dib_size = (width, height)

        ctypes.memmove(self.overlay_dib_bits, bgra, len(bgra))
        gdi.GdiFlush()
        screen_dc = user32.GetDC(None)
        destination = _WinPoint(self.overlay.winfo_rootx(), self.overlay.winfo_rooty())
        size = _WinSize(width, height)
        source = _WinPoint(0, 0)
        blend = _WinBlend(0, 0, 255, 1)
        try:
            ok = user32.UpdateLayeredWindow(
                self.overlay_hwnd,
                screen_dc,
                ctypes.byref(destination),
                ctypes.byref(size),
                self.overlay_dib_dc,
                ctypes.byref(source),
                0,
                ctypes.byref(blend),
                2,
            )
        finally:
            user32.ReleaseDC(None, screen_dc)
        return bool(ok)

    def release_overlay_dib(self):
        if os.name == "nt":
            gdi = ctypes.windll.gdi32
            if self.overlay_dib_dc and self.overlay_dib_previous:
                gdi.SelectObject(self.overlay_dib_dc, self.overlay_dib_previous)
            if self.overlay_dib_bitmap:
                gdi.DeleteObject(self.overlay_dib_bitmap)
            if self.overlay_dib_dc:
                gdi.DeleteDC(self.overlay_dib_dc)
        self.overlay_dib_dc = None
        self.overlay_dib_bitmap = None
        self.overlay_dib_previous = None
        self.overlay_dib_bits = None
        self.overlay_dib_size = None

    def cancel_overlay_timer(self, attribute):
        timer_id = getattr(self, attribute, None)
        if timer_id is not None:
            try:
                self.root.after_cancel(timer_id)
            except tk.TclError:
                pass
            setattr(self, attribute, None)

    def draw_download_overlay(self, force=False):
        if not self.overlay or not self.overlay.winfo_exists():
            return
        now = time.monotonic()
        min_interval = 1 / 30
        elapsed = now - self.overlay_last_draw_at
        if not force and elapsed < min_interval:
            if self.overlay_redraw_after_id is None:
                delay = max(1, round((min_interval - elapsed) * 1000))
                self.overlay_redraw_after_id = self.root.after(delay, self.draw_overlay_deferred)
            return
        self.cancel_overlay_timer("overlay_redraw_after_id")
        self.overlay_last_draw_at = now
        canvas = Image.new("RGBA", (260, 300), (0, 0, 0, 0))
        center_x, center_y = 130, 210
        if self.overlay_animation_frames:
            frame = self.overlay_animation_frames[self.overlay_frame_index][0]
            canvas.alpha_composite(frame, (center_x - frame.width // 2, center_y - frame.height // 2))
        else:
            fallback = getattr(self, "overlay_fallback_image", None)
            if fallback is None:
                try:
                    fallback = (
                        Image.open(APP_DIR / "Ploopy.png")
                        .convert("RGBA")
                        .resize((180, 180), Image.Resampling.LANCZOS)
                    )
                    self.overlay_fallback_image = fallback
                except (OSError, tk.TclError):
                    fallback = False
                    self.overlay_fallback_image = fallback
            if fallback:
                canvas.alpha_composite(fallback, (center_x - 90, center_y - 90))
            else:
                ImageDraw.Draw(canvas).text((center_x, center_y), "↓", fill="#2786ed", anchor="mm")

        # Progress border intentionally disabled: the compact tray animation
        # remains, without the non-moving green perimeter.

        if not self.update_layered_overlay(canvas):
            if not self.overlay_alpha_failed:
                self.overlay.configure(bg="#f3f7fc")
                self.overlay_alpha_failed = True

    def draw_overlay_deferred(self):
        self.overlay_redraw_after_id = None
        self.draw_download_overlay(force=True)

    def draw_square_progress_border(self):
        scale, width, height = 4, 260, 300
        logical_box = (59, 139, 201, 281)
        box = tuple(value * scale for value in logical_box)
        radius = 34 * scale
        stroke = 7 * scale
        if self.overlay_border_base is None:
            self.overlay_border_base = Image.new(
                "RGBA", (width * scale, height * scale), (0, 0, 0, 0)
            )
            ImageDraw.Draw(self.overlay_border_base).rounded_rectangle(
                box, radius=radius, outline="#edf2f7", width=stroke
            )
        completed = self.overlay_percent is not None and self.overlay_percent >= 100
        progress = max(0.0, min(100.0, float(self.overlay_percent or 0)))
        # Keep a visible green starting mark while yt-dlp is resolving a URL
        # and has not emitted its first numeric percentage yet.
        if self.is_downloading and not self.overlay_done and progress == 0:
            progress = 1.5
        cache_key = (
            ("complete",) if completed else ("progress", round(progress, 1), self.overlay_done)
        )
        if cache_key == self.overlay_border_cache_key and self.overlay_border_cache is not None:
            return self.overlay_border_cache
        image = self.overlay_border_base.copy()
        draw = ImageDraw.Draw(image)

        if completed:
            draw.rounded_rectangle(box, radius=radius, outline="#45c982", width=stroke)
        else:
            progress_fraction = progress / 100
            if progress_fraction > 0:
                path = self.rounded_square_path(logical_box, 34)
                points = self.square_path_segment(path, 0.0, progress_fraction)
                color = (
                    "#e45e69" if self.overlay_done else "#45c982"
                )
                self.draw_path_stroke(draw, points, color, stroke, scale)

        self.overlay_border_cache = (
            image.convert("RGBa")
            .resize((width, height), Image.Resampling.LANCZOS)
            .convert("RGBA")
        )
        self.overlay_border_cache_key = cache_key
        return self.overlay_border_cache

    @staticmethod
    def rounded_square_path(box, radius):
        x1, y1, x2, y2 = box
        points = []

        def add_line(start, end):
            length = math.hypot(end[0] - start[0], end[1] - start[1])
            count = max(1, math.ceil(length / 4))
            for index in range(1, count + 1):
                t = index / count
                points.append(
                    (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
                )

        def add_arc(cx, cy, start, end):
            count = max(1, math.ceil(abs(end - start) / 5))
            for index in range(1, count + 1):
                angle = math.radians(start + (end - start) * index / count)
                points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))

        start = ((x1 + x2) / 2, y1)
        points.append(start)
        add_line(start, (x2 - radius, y1))
        add_arc(x2 - radius, y1 + radius, -90, 0)
        add_line((x2, y1 + radius), (x2, y2 - radius))
        add_arc(x2 - radius, y2 - radius, 0, 90)
        add_line((x2 - radius, y2), (x1 + radius, y2))
        add_arc(x1 + radius, y2 - radius, 90, 180)
        add_line((x1, y2 - radius), (x1, y1 + radius))
        add_arc(x1 + radius, y1 + radius, 180, 270)
        add_line((x1 + radius, y1), start)
        return points

    @staticmethod
    def square_path_segment(points, start_fraction, segment_fraction):
        lengths = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
        total = sum(lengths)
        if total <= 0:
            return []

        def point_at(distance):
            distance = max(0.0, min(total, distance))
            for first, second, length in zip(points, points[1:], lengths):
                if distance <= length:
                    t = distance / length if length else 0
                    return (
                        first[0] + (second[0] - first[0]) * t,
                        first[1] + (second[1] - first[1]) * t,
                    )
                distance -= length
            return points[-1]

        start = (start_fraction % 1.0) * total
        amount = min(1.0, max(0.0, segment_fraction)) * total
        end = start + amount
        intervals = max(2, math.ceil(amount / 4))
        if end <= total:
            return [point_at(start + amount * i / intervals) for i in range(intervals + 1)]
        first_part = total - start
        first_count = max(1, round(intervals * first_part / amount))
        points_a = [point_at(start + first_part * i / first_count) for i in range(first_count + 1)]
        remaining = end - total
        second_count = max(1, intervals - first_count)
        points_b = [point_at(remaining * i / second_count) for i in range(1, second_count + 1)]
        return points_a + points_b

    @staticmethod
    def draw_path_stroke(draw, points, color, stroke, scale):
        if len(points) < 2:
            return
        high_res = [(round(x * scale), round(y * scale)) for x, y in points]
        draw.line(high_res, fill=color, width=stroke, joint="curve")
        radius = stroke / 2
        for x, y in (high_res[0], high_res[-1]):
            draw.ellipse(
                (round(x - radius), round(y - radius), round(x + radius), round(y + radius)),
                fill=color,
            )

    def animate_download_overlay(self):
        self.overlay_after_id = None
        if not self.overlay or not self.overlay.winfo_exists():
            return
        if not self.overlay_animation_loaded:
            self.overlay_after_id = self.root.after(63, self.animate_download_overlay)
            return
        if self.overlay_animation_frames and self.overlay_next_frame_at <= 0:
            self.overlay_frame_index = 0
            self.overlay_next_frame_at = (
                time.monotonic() + self.overlay_animation_frames[0][1] / 1000
            )
        if self.overlay_done:
            self.draw_download_overlay()
            generation = self.overlay_generation
            self.overlay_hide_after_id = self.root.after(
                5000, lambda current=generation: self.hide_overlay(current)
            )
            return
        if not self.is_downloading:
            return
        now = time.monotonic()
        frame_changed = False
        if self.overlay_animation_frames:
            while now >= self.overlay_next_frame_at:
                self.overlay_frame_index = (self.overlay_frame_index + 1) % len(
                    self.overlay_animation_frames
                )
                duration = self.overlay_animation_frames[self.overlay_frame_index][1] / 1000
                self.overlay_next_frame_at += duration
                frame_changed = True

        next_due = (
            self.overlay_next_frame_at if self.overlay_animation_frames else float("inf")
        )
        delay_ms = (
            max(1, round((next_due - time.monotonic()) * 1000))
            if next_due != float("inf")
            else 63
        )
        # Schedule the next tick before rendering. If a single Windows layered
        # window redraw fails, it must not permanently stop the GIF loop.
        self.overlay_after_id = self.root.after(delay_ms, self.animate_download_overlay)
        if frame_changed:
            try:
                self.draw_download_overlay()
            except (tk.TclError, OSError, ValueError, ctypes.ArgumentError):
                pass

    def hide_overlay(self, generation=None):
        if generation is not None and generation != self.overlay_generation:
            return
        self.cancel_overlay_timer("overlay_after_id")
        self.cancel_overlay_timer("overlay_hide_after_id")
        self.cancel_overlay_timer("overlay_redraw_after_id")
        if self.overlay and self.overlay.winfo_exists():
            self.overlay.destroy()
        self.overlay = None
        self.overlay_canvas = None
        self.overlay_hwnd = None
        self.release_overlay_dib()
        self.overlay_alpha_failed = False

    def choose_dir(self):
        selected = filedialog.askdirectory(initialdir=self.download_dir.get())
        if selected:
            self.download_dir.set(selected)

    def open_dir(self):
        path = Path(self.download_dir.get()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def hotkey(self, _event=None):
        self.download_clipboard()
        return "break"

    def paste_clipboard(self):
        try:
            value = self.root.clipboard_get().strip()
        except tk.TclError:
            self.status_detail.set("Буфер обмена пуст")
            return
        self.clear_placeholder()
        self.url_entry.delete(0, "end")
        self.url_entry.insert(0, value)
        self.url_entry.focus_set()

    def download_clipboard(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            self.status_detail.set("Буфер обмена пуст или содержит неподдерживаемый формат")
            return
        match = URL_RE.search(text.strip())
        if not match:
            self.status_detail.set("В буфере не найдена ссылка http:// или https://")
            return
        self.enqueue(match.group(0).rstrip(".,);]"))

    def submit_entry(self, _event=None):
        value = self.url_entry.get().strip()
        if value == self.ui_customization.get("placeholder", "Вставь ссылку..."):
            value = ""
        self.enqueue(value)

    def enqueue(self, url: str):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            self.status_detail.set("Введи ссылку, начинающуюся с http:// или https://")
            return
        self.jobs.put(url)
        self.create_download_row(url)
        self.url_entry.delete(0, "end")
        self.url_entry.configure(fg="#182238")
        self.status_detail.set("Ссылка добавлена в очередь")

    def worker(self):
        while True:
            url = self.jobs.get()
            try:
                if url in getattr(self, "cancelled_urls", set()):
                    self.cancelled_urls.discard(url)
                    self.events.put(("cancelled", url))
                    continue
                self.cancel_requested = False
                self.pause_event.set()
                self.events.put(("start", url))
                folder = Path(self.download_dir.get()).expanduser()
                folder.mkdir(parents=True, exist_ok=True)
                if self.is_video_site(url):
                    self.download_video(url, folder)
                else:
                    self.download_direct(url, folder)
                self.events.put(("done", url))
            except DownloadCancelled:
                self.events.put(("cancelled", url))
            except Exception as exc:
                self.events.put(("error", (url, str(exc))))
            finally:
                self.current_process = None
                self.pause_event.set()
                self.jobs.task_done()

    def toggle_pause(self, url):
        if url != self.current_url:
            return
        row = self.download_rows.get(url)
        if self.pause_event.is_set():
            self.pause_event.clear()
            if self.current_process is not None:
                self.set_process_suspended(self.current_process, True)
            if row:
                row["detail"].configure(text="Приостановлено", fg="#b07a25")
        else:
            if self.current_process is not None:
                self.set_process_suspended(self.current_process, False)
            self.pause_event.set()
            if row:
                row["detail"].configure(text="Скачивается…", fg="#67758e")

    @staticmethod
    def set_process_suspended(process, suspended):
        try:
            ntdll = ctypes.WinDLL("ntdll")
            api = ntdll.NtSuspendProcess if suspended else ntdll.NtResumeProcess
            api.argtypes = (ctypes.wintypes.HANDLE,)
            api.restype = ctypes.c_long
            api(ctypes.wintypes.HANDLE(process._handle))
        except (AttributeError, OSError):
            pass

    def cancel_download(self, url):
        if url != self.current_url:
            self.cancelled_urls = getattr(self, "cancelled_urls", set())
            self.cancelled_urls.add(url)
            self.update_download_row(url, detail="Отменено", finished=True)
            return
        self.cancel_requested = True
        self.pause_event.set()
        process = self.current_process
        if process and process.poll() is None:
            process.terminate()

    @staticmethod
    def is_video_site(url: str) -> bool:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        known = (
            "youtube.com",
            "youtu.be",
            "tiktok.com",
            "instagram.com",
            "facebook.com",
            "x.com",
            "twitter.com",
            "vimeo.com",
            "twitch.tv",
            "reddit.com",
        )
        return any(host == domain or host.endswith("." + domain) for domain in known)

    def download_video(self, url: str, folder: Path):
        target = folder / "%(title).180B.%(ext)s"
        quality_limit = {"720p — быстрее": 720, "1080p — чётче": 1080}.get(self.quality.get())
        if quality_limit:
            format_spec = f"bestvideo*[height<={quality_limit}]+bestaudio/best[height<={quality_limit}]/best"
        else:
            format_spec = "bestvideo*+bestaudio/best"
        args = [
            str(APP_DIR / "yt-dlp.exe") if getattr(sys, "frozen", False) else sys.executable,
            *([] if getattr(sys, "frozen", False) else ["-m", "yt_dlp"]),
            "--no-playlist",
            "--windows-filenames",
            "--concurrent-fragments",
            "16",
            "--newline",
            "--no-colors",
            "--http-chunk-size",
            "10M",
            "-f",
            format_spec,
            "--merge-output-format",
            "mp4",
            "--print",
            "before_dl:__PLOOPY_TITLE__%(title)s",
            "--print",
            "after_move:filepath",
            "-o",
            str(target),
        ]
        browser_setting = self.browser.get()
        if browser_setting == "Автоматически":
            browsers = ["Chrome", "Edge", "Firefox", "Brave", "Opera", "Без авторизации"]
        else:
            browsers = [browser_setting]
            if browser_setting != "Без авторизации":
                browsers.append("Без авторизации")

        return_code = 1
        output_lines = []
        final_browser = browsers[0]
        for index, browser in enumerate(browsers):
            attempt_args = list(args)
            if browser != "Без авторизации":
                attempt_args.extend(["--cookies-from-browser", browser.lower()])
            attempt_args.append(url)
            if index == 0:
                message = "Подготавливаю загрузку…"
            elif browser == "Без авторизации":
                message = "Пробую скачать без входа…"
            else:
                message = f"Пробую сессию {browser}…"
            self.events.put(("progress", (None, message)))
            return_code, output_lines = self.run_ytdlp(attempt_args)
            final_browser = browser
            if not return_code:
                self.emit_downloaded_file(output_lines, folder)
                return

            attempt_text = "\n".join(output_lines).lower()
            may_need_another_session = any(
                marker in attempt_text
                for marker in (
                    "could not copy",
                    "cookie database",
                    "sign in to confirm",
                    "not a bot",
                    "no video formats found",
                    "login required",
                    "authentication required",
                )
            )
            if not may_need_another_session or index == len(browsers) - 1:
                break

        if return_code:
            details = "\n".join(output_lines).strip()
            useful = [
                line.strip()
                for line in details.splitlines()
                if "ERROR:" in line or "WARNING:" in line
            ]
            explanation = useful[-1] if useful else (details.splitlines()[-1] if details else "Сайт не вернул подробности ошибки.")
            if "sign in to confirm" in explanation.lower() or "not a bot" in explanation.lower():
                if browser_setting == "Автоматически":
                    explanation = (
                        "Сайт требует входа. Ploopy проверил сохранённые сессии браузеров "
                        "и попробовал без входа, но сайт всё равно отказал. "
                        "Убедись, что в одном из браузеров выполнен вход."
                    )
                elif final_browser == "Без авторизации":
                    explanation = (
                        "YouTube требует входа и отклонил загрузку без авторизации. "
                        "Войди в YouTube в браузере, затем выбери этот браузер в настройке "
                        "«Вход через браузер» и повтори загрузку."
                    )
                else:
                    explanation = (
                        f"YouTube требует подтверждения входа через {final_browser}. "
                        "Проверь, что в выбранном профиле открыт YouTube и выполнен вход. "
                        "Если Ploopy не смог прочитать cookies, полностью закрой браузер и повтори загрузку."
                    )
            elif "cookies" in explanation.lower():
                explanation += " Проверь, что в одном из браузеров выполнен вход; если cookies заблокированы, закрой его и повтори попытку."
            elif "unavailable" in explanation.lower() or "private" in explanation.lower():
                explanation += (
                    " Ролик удалён, закрыт автором или недоступен для этого аккаунта/региона."
                )
            raise RuntimeError(explanation[:500])

    def emit_downloaded_file(self, output_lines, folder):
        for line in reversed(output_lines):
            candidate = Path(line.strip().strip('"'))
            try:
                if not candidate.is_absolute():
                    candidate = folder / candidate
                if candidate.is_file() and candidate.parent.resolve() == folder.resolve():
                    self.events.put(
                        ("file", (candidate.name, self.format_size(candidate.stat().st_size)))
                    )
                    return
            except OSError:
                continue

    def run_ytdlp(self, args):
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.current_process = proc
        if not self.pause_event.is_set():
            self.set_process_suspended(proc, True)
        output_lines = []
        format_spec = args[args.index("-f") + 1] if "-f" in args else ""
        expected_parts = 2 if "+" in format_spec else 1
        part_progress = {}
        current_part = "download"
        for line in proc.stdout:
            self.pause_event.wait()
            if self.cancel_requested:
                if proc.poll() is None:
                    proc.terminate()
                break
            line = line.strip()
            output_lines.append(line)
            if line.startswith("__PLOOPY_TITLE__"):
                title = line.removeprefix("__PLOOPY_TITLE__").strip()
                if title:
                    self.events.put(("title", (self.current_url, title)))
            if line.startswith("[download] Destination:"):
                current_part = (
                    line.split("Destination:", 1)[1].strip() or f"part-{len(part_progress)}"
                )
                part_progress.setdefault(current_part, 0.0)
            if line.startswith("[download]"):
                match = re.search(r"(\d+(?:\.\d+)?)%", line)
                if match:
                    part_progress[current_part] = max(
                        part_progress.get(current_part, 0.0),
                        max(0.0, min(100.0, float(match.group(1)))),
                    )
                    divisor = max(expected_parts, len(part_progress))
                    percent = sum(part_progress.values()) / divisor
                else:
                    percent = None
                size_match = re.search(r"\bof\s+~?([^\s]+)", line)
                eta_match = re.search(r"\bETA\s+([^\s]+)", line)
                size_str = size_match.group(1) if size_match else "Скачивание"
                eta_str = f"  ·  ~ {eta_match.group(1)}" if eta_match else ""
                self.events.put(("progress", (percent, f"{size_str}{eta_str}")))
            elif "Merging formats" in line or "[Merger]" in line:
                self.events.put(("progress", (None, "Объединяю видео и звук…")))
        return_code = proc.wait()
        self.current_process = None
        if self.cancel_requested:
            raise DownloadCancelled()
        return return_code, output_lines

    def download_direct(self, url: str, folder: Path):
        request = urllib.request.Request(url, headers={"User-Agent": "LinkDownloader/1.0"})
        with urllib.request.urlopen(request, timeout=45) as response:
            filename = self.filename_from_headers(response.headers, url)
            dest = self.unique_path(folder / filename)
            total = int(response.headers.get("Content-Length", "0") or 0)
            received = 0
            started = time.monotonic()
            with dest.open("wb") as output:
                while True:
                    self.pause_event.wait()
                    if self.cancel_requested:
                        raise DownloadCancelled()
                    block = response.read(1024 * 256)
                    if not block:
                        break
                    output.write(block)
                    received += len(block)
                    percent = received * 100 / total if total else None
                    elapsed = max(0.001, time.monotonic() - started)
                    speed = received / elapsed
                    if total and speed > 0:
                        rem = max(0, int((total - received) / speed))
                        detail = f"{self.format_size(total)}  ·  ~ {rem} сек"
                    else:
                        detail = f"{self.format_size(received)}"
                    self.events.put(("progress", (percent, detail)))
        self.events.put(("file", (dest.name, self.format_size(dest.stat().st_size))))

    @staticmethod
    def filename_from_headers(headers, url: str) -> str:
        disposition = headers.get("Content-Disposition", "")
        match = re.search(r"filename\*=UTF-8''([^;]+)|filename=[\"']?([^\"';]+)", disposition, re.I)
        name = urllib.parse.unquote(match.group(1) or match.group(2)).strip() if match else ""
        if not name:
            name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name or "download"
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
        return name[:180] or "download"

    @staticmethod
    def unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        for index in range(1, 10000):
            candidate = path.with_name(f"{path.stem} ({index}){path.suffix}")
            if not candidate.exists():
                return candidate
        raise RuntimeError("Слишком много файлов с таким именем")

    def mark_queue(self, url: str, label: str):
        self.update_download_row(url, detail=label)

    def process_events(self):
        while True:
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "start":
                self.is_downloading = True
                self.set_progress(0)
                self.current_url = value
                self.progress_title.set("Идёт скачивание")
                self.progress_percent.set("…")
                self.status_detail.set(value)
                self.mark_queue(value, "Скачивается…")
                self.update_download_row(value, progress=0)
                self.show_download_overlay("Загрузка началась", value, None)
            elif kind == "progress":
                percent, detail = value
                self.status_detail.set(detail)
                if percent is not None:
                    self.set_progress(percent)
                    self.progress_percent.set(f"{percent:.0f}%")
                    self.update_download_row(self.current_url, detail=detail, progress=percent)
                else:
                    self.progress_percent.set("…")
                    self.update_download_row(self.current_url, detail=detail)
                if self.is_downloading:
                    self.show_download_overlay("Идёт скачивание", detail, percent)
            elif kind == "file":
                title, size = value
                self.update_download_row(
                    self.current_url, title=title, size=size, detail=f"{size}  ·  Готово"
                )
            elif kind == "title":
                url, title = value
                if url and title:
                    self.update_download_row(url, title=title)
            elif kind == "done":
                self.is_downloading = False
                self.mark_queue(value, "Готово")
                row = self.download_rows.get(value)
                title = row.get("full_title") if row else self.title_from_url(value)
                title = title or self.title_from_url(value)
                row_size = row.get("size", "Готово") if row else "Готово"
                self.update_download_row(
                    value,
                    title=title,
                    detail=f"{row_size}  ·  Готово",
                    progress=100,
                    finished=True,
                )
                self.add_history_item(title, row_size)
                self.set_progress(100)
                self.progress_title.set("Загрузка завершена")
                self.progress_percent.set("100%")
                self.status_detail.set("Файл сохранён в выбранную папку")
                self.hide_overlay()
                if self.settings.get("sound", True):
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                self.current_url = None
            elif kind == "cancelled":
                self.is_downloading = False
                self.update_download_row(value, detail="Отменено", finished=True)
                self.progress_title.set("Загрузка отменена")
                self.progress_percent.set("—")
                self.status_detail.set("Загрузка остановлена")
                self.show_download_overlay(
                    "Загрузка отменена", "Загрузка остановлена", 0, done=True
                )
                self.current_url = None
            elif kind == "error":
                url, error = value
                self.is_downloading = False
                self.mark_queue(url, "Ошибка")
                self.update_download_row(url, detail=error[:100], finished=True)
                self.progress_title.set("Не удалось скачать")
                self.progress_percent.set("!")
                self.status_detail.set(error)
                self.show_download_overlay("Не удалось скачать", error, 0, done=True)
                self.current_url = None
        self.root.after(150, self.process_events)

def main():
    mutex_handle = None
    kernel32 = None
    try:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (
                ctypes.c_void_p,
                ctypes.wintypes.BOOL,
                ctypes.wintypes.LPCWSTR,
            )
            kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
            kernel32.CloseHandle.argtypes = (ctypes.wintypes.HANDLE,)
            kernel32.CloseHandle.restype = ctypes.wintypes.BOOL
            ctypes.set_last_error(0)
            mutex_handle = kernel32.CreateMutexW(
                None, False, "Local\\Ploopy.Downloader.Singleton"
            )
            mutex_error = ctypes.get_last_error()
            if not mutex_handle:
                raise ctypes.WinError(mutex_error)
            if mutex_error == 183:  # ERROR_ALREADY_EXISTS
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.MessageBoxW.argtypes = (
                    ctypes.wintypes.HWND,
                    ctypes.wintypes.LPCWSTR,
                    ctypes.wintypes.LPCWSTR,
                    ctypes.wintypes.UINT,
                )
                user32.MessageBoxW.restype = ctypes.c_int
                user32.MessageBoxW(
                    None,
                    "Ploopy уже открыто. Найди приложение в системном трее.",
                    "Ploopy",
                    0x40,
                )
                return

        root = tk.Tk()
        app = DownloaderApp(root)
        root.update_idletasks()
        root.deiconify()
        root.lift()
        root.focus_force()
        root.mainloop()
    except Exception:
        log_path = APP_DIR / "startup.log"
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        if mutex_handle and kernel32 is not None:
            kernel32.CloseHandle(mutex_handle)


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        required = (APP_DIR / "Ploopy.png", APP_DIR / "Ploopy.ico", APP_DIR / "yt-dlp.exe", APP_DIR / "version.txt", FONT_DIR / "Manrope-Regular.ttf", SVG_ICON_DIR / "download.svg")
        if not all(path.is_file() for path in required):
            raise SystemExit(2)
        try:
            import pystray
            from PIL import Image
            parse_hotkey(DEFAULT_HOTKEY)
        except Exception:
            raise SystemExit(3)
        raise SystemExit(0)
    main()





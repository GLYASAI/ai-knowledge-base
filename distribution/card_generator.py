"""小红书图文卡片生成器：仿「每周开源大赏」风格，Pillow 渲染 1080×1440。

纯函数模块，不发网络请求。github_meta 由调用方注入。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CARD_W = 1080
CARD_H = 1440

# ── 配色 ──────────────────────────────────────────────────────────────────────
_DARK    = (43, 26, 8)
_CREAM   = (250, 246, 238)
_LCREAM  = (240, 232, 215)
_GOLD    = (196, 152, 0)
_WTEXT   = (245, 230, 200)
_DTEXT   = (26, 16, 8)
_MUTED   = (139, 115, 85)
_DIVIDER = (210, 195, 170)
_BADGE   = (220, 210, 195)

_AUDIENCE = {
    "beginner":     "AI 入门学习者、对新技术感兴趣的学生",
    "intermediate": "AI 工程师、开发者、技术创业者",
    "advanced":     "AI 研究者、资深工程师、开源贡献者",
}

_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode MS.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap(text: str, font: Any, max_w: int,
          draw: ImageDraw.ImageDraw) -> list[str]:
    """按像素宽度折行，CJK 逐字、ASCII 单词整体不拆断。"""
    lines: list[str] = []
    for para in text.split("\n"):
        # 将段落拆为 token：CJK 字符单独成 token，ASCII 连续串整体为 token
        tokens = re.findall(r"[^\x00-\x7F]|[\x00-\x7F]+", para)
        cur = ""
        for token in tokens:
            candidate = cur + token
            if draw.textlength(candidate, font=font) <= max_w:
                cur = candidate
            else:
                if cur:
                    lines.append(cur)
                # 若单个 token 超宽（长英文），强制逐字拆
                if draw.textlength(token, font=font) > max_w:
                    for ch in token:
                        if draw.textlength(cur + ch, font=font) > max_w and cur:
                            lines.append(cur)
                            cur = ch
                        else:
                            cur += ch
                else:
                    cur = token
        if cur:
            lines.append(cur)
    return lines


def _fmt(n: int | None) -> str:
    if n is None:
        return "-"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _section_label(draw: ImageDraw.ImageDraw, x: int, y: int,
                   text: str, font: Any, color: tuple, bar_color: tuple) -> None:
    """绘制带色块前缀的节标题（代替渲染不稳定的 Unicode 符号）。"""
    draw.rectangle([(x, y + 4), (x + 5, y + 30)], fill=bar_color)
    draw.text((x + 14, y), text, font=font, fill=color)


def generate_card(
    article: dict[str, Any],
    output_path: Path,
    github_meta: dict[str, Any] | None = None,
) -> Path:
    """渲染单篇知识条目为小红书图文卡片并保存为 PNG。

    Args:
        article: 符合项目知识条目格式的 dict。
        output_path: 输出文件路径（父目录不存在时自动创建）。
        github_meta: GitHub API 返回的仓库元数据（可选）。

    Returns:
        实际写入的文件路径。
    """
    title      = article.get("title", "")
    summary    = article.get("summary", "")
    source_url = article.get("source_url", "")
    analysis   = article.get("analysis", {})
    highlights = analysis.get("tech_highlights", [])
    score_rsn  = analysis.get("score_reason", "")
    audience   = analysis.get("audience", "intermediate")
    tags       = article.get("tags", [])

    gm       = github_meta or {}
    stars    = gm.get("stargazers_count")
    forks    = gm.get("forks_count")
    language = gm.get("language") or "-"
    license_ = (gm.get("license") or {}).get("spdx_id") or "开源协议"
    gm_topics: list[str] = gm.get("topics", [])

    hl_title      = (score_rsn[:12] if score_rsn else summary[:10]).rstrip("，,。. ")
    audience_text = _AUDIENCE.get(audience, _AUDIENCE["intermediate"])
    all_tags      = list(dict.fromkeys(gm_topics[:4] + tags))[:6]

    img  = Image.new("RGB", (CARD_W, CARD_H), _CREAM)
    draw = ImageDraw.Draw(img)

    PAD = 52
    f60 = _font(60)
    f48 = _font(48)
    f38 = _font(38)
    f30 = _font(30)
    f28 = _font(28)
    f24 = _font(24)

    # ════════════════════════════════════════════════════════════════════════
    # 1. 顶部系列标题栏
    # ════════════════════════════════════════════════════════════════════════
    HDR_H = 108
    draw.rectangle([(0, 0), (CARD_W, HDR_H)], fill=_DARK)
    draw.text((PAD, 22), "AI 知识日报", font=f60, fill=_WTEXT)

    # ════════════════════════════════════════════════════════════════════════
    # 2. 项目信息区
    # ════════════════════════════════════════════════════════════════════════
    INFO_Y = HDR_H
    INFO_H = 148
    draw.rectangle([(0, INFO_Y), (CARD_W, INFO_Y + INFO_H)], fill=_CREAM)

    av = 84
    ax = PAD
    ay = INFO_Y + (INFO_H - av) // 2
    draw.rounded_rectangle([(ax, ay), (ax + av, ay + av)], radius=14, fill=_DARK)
    letter = (title[0] if title and title[0].isascii() else "A").upper()
    lw = draw.textlength(letter, font=f48)
    draw.text((ax + (av - lw) / 2, ay + 14), letter, font=f48, fill=_WTEXT)

    tx = ax + av + 22
    ty = INFO_Y + 16
    for line in _wrap(title, f38, CARD_W - tx - PAD, draw)[:2]:
        draw.text((tx, ty), line, font=f38, fill=_DTEXT)
        ty += 50

    badge_text = f"开源协议 {license_}"
    bw = int(draw.textlength(badge_text, font=f24)) + 24
    bh = 34
    by = INFO_Y + INFO_H - bh - 12
    draw.rounded_rectangle([(tx, by), (tx + bw, by + bh)], radius=6, fill=_BADGE)
    draw.text((tx + 12, by + 5), badge_text, font=f24, fill=_MUTED)

    # ════════════════════════════════════════════════════════════════════════
    # 3. 统计栏
    # ════════════════════════════════════════════════════════════════════════
    STAT_Y = INFO_Y + INFO_H
    STAT_H = 100
    draw.rectangle([(0, STAT_Y), (CARD_W, STAT_Y + STAT_H)], fill=_CREAM)
    draw.line([(0, STAT_Y), (CARD_W, STAT_Y)], fill=_DIVIDER, width=2)
    draw.line([(0, STAT_Y + STAT_H), (CARD_W, STAT_Y + STAT_H)], fill=_DIVIDER, width=2)

    stats = [
        ("STAR",  _fmt(stars)),
        ("语言",   language),
        ("FORKS", _fmt(forks)),
        ("相关性", f"{analysis.get('relevance_score', 0)}/10"),
    ]
    col_w = CARD_W // 4
    for i, (label, value) in enumerate(stats):
        cx = i * col_w + col_w // 2
        lw2 = draw.textlength(label, font=f24)
        vw  = draw.textlength(value, font=f38)
        draw.text((cx - lw2 / 2, STAT_Y + 12), label, font=f24, fill=_MUTED)
        draw.text((cx - vw  / 2, STAT_Y + 50), value,  font=f38, fill=_DTEXT)
        if i > 0:
            draw.line([(i * col_w, STAT_Y + 18), (i * col_w, STAT_Y + STAT_H - 18)],
                      fill=_DIVIDER, width=1)

    # ════════════════════════════════════════════════════════════════════════
    # 4. 主内容双栏
    # ════════════════════════════════════════════════════════════════════════
    BODY_Y = STAT_Y + STAT_H
    FOOT_H = 64
    LEFT_W = 400
    RIGHT_W = CARD_W - LEFT_W

    draw.rectangle([(0,      BODY_Y), (LEFT_W,  CARD_H - FOOT_H)], fill=_LCREAM)
    draw.rectangle([(LEFT_W, BODY_Y), (CARD_W,  CARD_H - FOOT_H)], fill=_DARK)

    # ── 左栏：核心功能 ────────────────────────────────────────────────────────
    LPAD = 30
    usable_l = LEFT_W - LPAD * 2 - 22   # 22 = bullet width
    y = BODY_Y + 32
    _section_label(draw, LPAD, y, "核心功能", f30, _GOLD, _GOLD)
    y += 50

    for hl in highlights[:6]:
        draw.text((LPAD, y), "◆", font=f28, fill=_GOLD)
        for j, line in enumerate(_wrap(hl, f28, usable_l, draw)[:3]):
            draw.text((LPAD + 22, y), line, font=f28, fill=_DTEXT)
            y += 44
        y += 8

    # ── 右栏：项目亮点 + 适用人群 + 标签 ─────────────────────────────────────
    RPAD = 30
    rx   = LEFT_W + RPAD
    rw   = RIGHT_W - RPAD * 2
    ry   = BODY_Y + 32

    _section_label(draw, rx, ry, "项目亮点", f30, _GOLD, _GOLD)
    ry += 50

    for line in _wrap(hl_title, f48, rw, draw)[:2]:
        draw.text((rx, ry), line, font=f48, fill=_WTEXT)
        ry += 64
    ry += 8

    for line in _wrap(summary, f28, rw, draw)[:10]:
        draw.text((rx, ry), line, font=f28, fill=_WTEXT)
        ry += 42
    ry += 20

    _section_label(draw, rx, ry, "适用人群", f30, _GOLD, _GOLD)
    ry += 50
    for line in _wrap(audience_text, f28, rw, draw)[:3]:
        draw.text((rx, ry), line, font=f28, fill=_WTEXT)
        ry += 42
    ry += 20

    if all_tags:
        _section_label(draw, rx, ry, "相关标签", f30, _GOLD, _GOLD)
        ry += 50
        tag_line = "  ".join(f"#{t}" for t in all_tags)
        for line in _wrap(tag_line, f28, rw, draw)[:3]:
            draw.text((rx, ry), line, font=f28, fill=_WTEXT)
            ry += 42

    # ════════════════════════════════════════════════════════════════════════
    # 5. 底部版权行
    # ════════════════════════════════════════════════════════════════════════
    FOOT_Y = CARD_H - FOOT_H
    draw.line([(0, FOOT_Y), (CARD_W, FOOT_Y)], fill=_DIVIDER, width=1)

    repo = source_url.replace("https://github.com/", "")
    draw.text((PAD, FOOT_Y + 18), repo[:44], font=f24, fill=_MUTED)
    wm = "小红书号: AI知识日报"
    wm_w = draw.textlength(wm, font=f24)
    draw.text((CARD_W - PAD - wm_w, FOOT_Y + 18), wm, font=f24, fill=_MUTED)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    logger.info("卡片已生成: %s", output_path)
    return output_path

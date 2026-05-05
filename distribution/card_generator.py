"""小红书图文卡片生成器：用 Pillow 将知识条目渲染为 1080×1440 图片。

纯函数模块，不发网络请求。github_meta 由调用方注入。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CARD_W = 1080
CARD_H = 1440
PAD = 64

# 配色
_BG         = (250, 248, 245)
_HEADER_BG  = (22, 22, 22)
_WHITE      = (255, 255, 255)
_GRAY_DARK  = (38, 38, 38)
_GRAY_MID   = (100, 100, 100)
_GRAY_LIGHT = (180, 178, 174)
_DIVIDER    = (218, 215, 210)
_RED        = (255, 48, 48)
_STAT_BG    = (240, 237, 232)

_SCORE_COLOR = {
    "green":  (40, 167, 69),
    "yellow": (255, 193, 7),
    "red":    (220, 53, 69),
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
    logger.warning("未找到中文字体，使用 Pillow 内置字体")
    return ImageFont.load_default()


def _wrap(text: str, font: Any, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """按像素宽度折行。"""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for ch in para:
            if draw.textlength(cur + ch, font=font) > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
    return lines


def _fmt_num(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def generate_card(
    article: dict[str, Any],
    output_path: Path,
    github_meta: dict[str, Any] | None = None,
) -> Path:
    """将单篇知识条目渲染为小红书图文卡片并保存为 PNG。

    Args:
        article: 符合项目知识条目格式的 dict。
        output_path: 输出文件路径（父目录不存在时自动创建）。
        github_meta: GitHub API 返回的仓库元数据（可选）。

    Returns:
        实际写入的文件路径。
    """
    title      = article.get("title", "（无标题）")
    summary    = article.get("summary", "")
    source_url = article.get("source_url", "")
    score      = article.get("analysis", {}).get("relevance_score", 0)
    highlights = article.get("analysis", {}).get("tech_highlights", [])
    tags       = article.get("tags", [])

    gm = github_meta or {}
    stars    = gm.get("stargazers_count")
    forks    = gm.get("forks_count")
    language = gm.get("language", "")
    topics   = gm.get("topics", [])
    updated  = (gm.get("updated_at") or "")[:10]

    img  = Image.new("RGB", (CARD_W, CARD_H), _BG)
    draw = ImageDraw.Draw(img)
    cw   = CARD_W - PAD * 2  # usable content width

    f56 = _font(56)
    f34 = _font(34)
    f28 = _font(28)
    f24 = _font(24)
    f22 = _font(22)

    # ── 顶部品牌色条 ─────────────────────────────────────────────────────────
    draw.rectangle([(0, 0), (CARD_W, 8)], fill=_RED)

    # ── Header 背景 ──────────────────────────────────────────────────────────
    HEADER_H = 280
    draw.rectangle([(0, 8), (CARD_W, HEADER_H)], fill=_HEADER_BG)

    # 标题（白色，最多 3 行）
    title_lines = _wrap(title, f56, cw, draw)[:3]
    y = 36
    for line in title_lines:
        draw.text((PAD, y), line, font=f56, fill=_WHITE)
        y += 70

    # ── GitHub 统计栏 ─────────────────────────────────────────────────────────
    STAT_H = 68
    draw.rectangle([(0, HEADER_H), (CARD_W, HEADER_H + STAT_H)], fill=_STAT_BG)

    stat_parts: list[str] = []
    if stars is not None:
        stat_parts.append(f"★ {_fmt_num(stars)}")
    if forks is not None:
        stat_parts.append(f"⑂ {_fmt_num(forks)}")
    if language:
        stat_parts.append(f"◈ {language}")
    if updated:
        stat_parts.append(f"↻ {updated}")

    stat_text = "    ".join(stat_parts) if stat_parts else ""
    if stat_text:
        draw.text((PAD, HEADER_H + 18), stat_text, font=f28, fill=_GRAY_MID)

    y = HEADER_H + STAT_H + 36

    # ── 摘要 ─────────────────────────────────────────────────────────────────
    for line in _wrap(summary, f34, cw, draw)[:8]:
        draw.text((PAD, y), line, font=f34, fill=_GRAY_DARK)
        y += 48
    y += 12

    # ── 技术亮点 ─────────────────────────────────────────────────────────────
    if highlights:
        draw.text((PAD, y), "技术亮点", font=f28, fill=_RED)
        y += 38
        for hl in highlights[:3]:
            prefix = "• "
            for i, line in enumerate(_wrap(prefix + hl, f28, cw, draw)[:3]):
                draw.text((PAD + (16 if i > 0 else 0), y), line, font=f28, fill=_GRAY_DARK)
                y += 38
        y += 8

    # ── Topics ───────────────────────────────────────────────────────────────
    all_tags = list(dict.fromkeys(topics[:3] + tags[:3]))  # 去重，topics 优先
    if all_tags:
        tag_str = "  ".join(f"#{t}" for t in all_tags[:5])
        for line in _wrap(tag_str, f24, cw, draw):
            draw.text((PAD, y), line, font=f24, fill=_RED)
            y += 34
        y += 8

    # ── 分割线 ───────────────────────────────────────────────────────────────
    div_y = max(y + 16, CARD_H - 180)
    draw.line([(PAD, div_y), (CARD_W - PAD, div_y)], fill=_DIVIDER, width=2)
    y = div_y + 24

    # ── 评分圆点 ─────────────────────────────────────────────────────────────
    if score >= 8:
        dot_color = _SCORE_COLOR["green"]
    elif score >= 6:
        dot_color = _SCORE_COLOR["yellow"]
    else:
        dot_color = _SCORE_COLOR["red"]

    r = 10
    cx, cy = PAD + r, y + r + 4
    draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=dot_color)
    draw.text((PAD + r * 2 + 10, y), f"相关性  {score}/10", font=f28, fill=_GRAY_MID)

    # ── 来源 URL（底部）──────────────────────────────────────────────────────
    url_display = source_url[:72] + "…" if len(source_url) > 72 else source_url
    draw.text((PAD, CARD_H - 52), url_display, font=f22, fill=_GRAY_LIGHT)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    logger.info("卡片已生成: %s", output_path)
    return output_path

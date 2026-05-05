"""小红书图文卡片生成器：用 Pillow 将知识条目渲染为 1080×1440 图片。

纯函数模块，不发网络请求。
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

CARD_W = 1080
CARD_H = 1440
PADDING = 64

# 配色
_BG = (250, 248, 245)
_HEADER_BG = (28, 28, 28)
_WHITE = (255, 255, 255)
_BODY = (38, 38, 38)
_META = (130, 130, 130)
_RED = (255, 55, 55)       # 小红书品牌红
_DIVIDER = (210, 208, 204)

_SCORE_COLORS = {
    "green": (52, 168, 83),
    "yellow": (251, 188, 4),
    "red": (234, 67, 53),
}

_FONT_CANDIDATES = [
    # Ubuntu / Debian（apt-get install fonts-noto-cjk）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    # macOS
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/Library/Fonts/Arial Unicode MS.ttf",
]


def _find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """按候选路径加载支持中文的字体，全部失败则用 Pillow 内置字体。

    Args:
        size: 字号（像素）。

    Returns:
        可用的 ImageFont 实例。
    """
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    logger.warning("未找到中文字体，使用 Pillow 内置字体（中文可能显示为方块）")
    return ImageFont.load_default()


def _score_dot_color(score: int | float) -> tuple[int, int, int]:
    if score >= 8:
        return _SCORE_COLORS["green"]
    if score >= 6:
        return _SCORE_COLORS["yellow"]
    return _SCORE_COLORS["red"]


def _wrap(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
          max_width: int, draw: ImageDraw.ImageDraw) -> list[str]:
    """按像素宽度自动折行。

    Args:
        text: 待折行文本。
        font: 使用的字体。
        max_width: 最大像素宽度。
        draw: ImageDraw 实例（用于测量文字宽度）。

    Returns:
        折行后的字符串列表。
    """
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for char in paragraph:
            test = current + char
            w = draw.textlength(test, font=font)
            if w > max_width and current:
                lines.append(current)
                current = char
            else:
                current = test
        if current:
            lines.append(current)
    return lines


def generate_card(article: dict[str, Any], output_path: Path) -> Path:
    """将单篇知识条目渲染为小红书图文卡片并保存为 PNG。

    Args:
        article: 符合项目知识条目格式的 dict。
        output_path: 输出文件路径（含文件名，父目录不存在时自动创建）。

    Returns:
        实际写入的文件路径。
    """
    title = article.get("title", "（无标题）")
    summary = article.get("summary", "")
    source_url = article.get("source_url", "")
    score = article.get("analysis", {}).get("relevance_score", 0)
    tags = article.get("tags", [])

    img = Image.new("RGB", (CARD_W, CARD_H), _BG)
    draw = ImageDraw.Draw(img)

    f_title = _find_font(54)
    f_body = _find_font(36)
    f_meta = _find_font(28)
    f_url = _find_font(22)

    content_w = CARD_W - PADDING * 2

    # ── 顶部品牌色条 ──────────────────────────────────────────────────────────
    draw.rectangle([(0, 0), (CARD_W, 10)], fill=_RED)

    # ── Header 背景 ──────────────────────────────────────────────────────────
    header_h = 300
    draw.rectangle([(0, 10), (CARD_W, header_h)], fill=_HEADER_BG)

    # 标题（白色，header 内自动折行，最多 3 行）
    title_lines = _wrap(title, f_title, content_w, draw)[:3]
    y = 40
    for line in title_lines:
        draw.text((PADDING, y), line, font=f_title, fill=_WHITE)
        y += 68

    # ── 摘要正文 ─────────────────────────────────────────────────────────────
    y = header_h + 48
    body_lines = _wrap(summary, f_body, content_w, draw)
    for line in body_lines[:14]:
        draw.text((PADDING, y), line, font=f_body, fill=_BODY)
        y += 52

    # ── 分割线 ───────────────────────────────────────────────────────────────
    divider_y = max(y + 40, header_h + 820)
    draw.line([(PADDING, divider_y), (CARD_W - PADDING, divider_y)],
              fill=_DIVIDER, width=2)
    y = divider_y + 32

    # ── 评分圆点 + 数值 ───────────────────────────────────────────────────────
    dot_r = 10
    dot_x, dot_y = PADDING + dot_r, y + dot_r + 4
    draw.ellipse(
        [(dot_x - dot_r, dot_y - dot_r), (dot_x + dot_r, dot_y + dot_r)],
        fill=_score_dot_color(score),
    )
    draw.text((PADDING + dot_r * 2 + 12, y), f"相关性  {score}/10",
              font=f_meta, fill=_META)
    y += 48

    # ── 标签 ─────────────────────────────────────────────────────────────────
    tags_str = "  ".join(f"#{t}" for t in tags[:5])
    if tags_str:
        draw.text((PADDING, y), tags_str, font=f_meta, fill=_RED)
        y += 44

    # ── 来源 URL（底部）──────────────────────────────────────────────────────
    url_display = source_url[:70] + "…" if len(source_url) > 70 else source_url
    draw.text((PADDING, CARD_H - 60), url_display, font=f_url, fill=_META)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    logger.info("卡片已生成: %s", output_path)
    return output_path

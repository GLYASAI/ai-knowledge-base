"""小红书图文卡片生成器：仿「每周开源大赏」风格，Pillow 渲染 1080×1440。

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

# ── 配色（参考「每周开源大赏」） ───────────────────────────────────────────────
_DARK    = (43, 26, 8)          # 深棕：顶栏、右栏底色
_CREAM   = (250, 246, 238)      # 奶油：卡片底色、左栏
_LCREAM  = (240, 232, 215)      # 浅棕：左栏区分背景
_GOLD    = (196, 152, 0)        # 金色：节名、子弹头
_WTEXT   = (245, 230, 200)      # 暖白：深色背景上的文字
_DTEXT   = (26, 16, 8)          # 深棕文字
_MUTED   = (139, 115, 85)       # 灰棕：统计栏标签
_GREEN   = (38, 140, 60)        # 绿色：周新增 Star
_DIVIDER = (210, 195, 170)      # 分隔线
_BADGE   = (220, 210, 195)      # 协议徽章底色

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


def _fmt(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


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

    gm       = github_meta or {}
    stars    = gm.get("stargazers_count")
    forks    = gm.get("forks_count")
    language = gm.get("language") or "—"
    license_ = (gm.get("license") or {}).get("spdx_id") or "开源协议"

    # 项目亮点大标题：取 score_reason 前 10 字，或 summary 前 8 字
    hl_title = (score_rsn[:10] if score_rsn else summary[:8]).rstrip("，,。. ")
    # 适用人群
    audience_text = _AUDIENCE.get(audience, _AUDIENCE["intermediate"])

    img  = Image.new("RGB", (CARD_W, CARD_H), _CREAM)
    draw = ImageDraw.Draw(img)

    PAD   = 52
    f60   = _font(60)
    f48   = _font(48)
    f38   = _font(38)
    f32   = _font(32)
    f28   = _font(28)
    f24   = _font(24)
    f20   = _font(20)

    # ════════════════════════════════════════════════════════════════════════
    # 1. 顶部系列标题栏
    # ════════════════════════════════════════════════════════════════════════
    HDR_H = 108
    draw.rectangle([(0, 0), (CARD_W, HDR_H)], fill=_DARK)
    draw.text((PAD, 24), "AI 知识日报", font=f60, fill=_WTEXT)

    # ════════════════════════════════════════════════════════════════════════
    # 2. 项目信息区（头像占位 + 名称 + 协议徽章）
    # ════════════════════════════════════════════════════════════════════════
    INFO_Y = HDR_H
    INFO_H = 148
    draw.rectangle([(0, INFO_Y), (CARD_W, INFO_Y + INFO_H)], fill=_CREAM)

    # 头像占位（圆角方块，取标题首字）
    av = 84
    ax, ay = PAD, INFO_Y + (INFO_H - av) // 2
    draw.rounded_rectangle([(ax, ay), (ax + av, ay + av)], radius=14, fill=_DARK)
    letter = title[0].upper() if title else "A"
    lw = draw.textlength(letter, font=f48)
    draw.text((ax + (av - lw) / 2, ay + 12), letter, font=f48, fill=_WTEXT)

    # 项目名 + 描述
    tx = ax + av + 22
    title_lines = _wrap(title, f38, CARD_W - tx - PAD, draw)[:2]
    ty = INFO_Y + 18
    for line in title_lines:
        draw.text((tx, ty), line, font=f38, fill=_DTEXT)
        ty += 50
    # 协议徽章
    badge_text = f"  ⑂ 开源协议 {license_}  "
    bw = int(draw.textlength(badge_text, font=f24)) + 4
    bh = 34
    by = INFO_Y + INFO_H - bh - 14
    draw.rounded_rectangle([(tx, by), (tx + bw, by + bh)], radius=6, fill=_BADGE)
    draw.text((tx + 2, by + 4), badge_text, font=f24, fill=_MUTED)

    # ════════════════════════════════════════════════════════════════════════
    # 3. 统计栏（周新增 / Star / 语言 / Forks）
    # ════════════════════════════════════════════════════════════════════════
    STAT_Y = INFO_Y + INFO_H
    STAT_H = 96
    draw.rectangle([(0, STAT_Y), (CARD_W, STAT_Y + STAT_H)], fill=_CREAM)
    # 顶部细分割线
    draw.line([(0, STAT_Y), (CARD_W, STAT_Y)], fill=_DIVIDER, width=2)
    draw.line([(0, STAT_Y + STAT_H)], fill=_DIVIDER, width=1)

    stats = [
        ("☆ STAR",   _fmt(stars),   _DTEXT),
        ("◈ 语言",    language,      _DTEXT),
        ("⑂ FORKS",  _fmt(forks),   _DTEXT),
        ("★ 相关性",  f"{article.get('analysis', {}).get('relevance_score', 0)}/10", _DTEXT),
    ]
    col_w = CARD_W // len(stats)
    for i, (label, value, vcol) in enumerate(stats):
        cx = i * col_w + col_w // 2
        lw2 = draw.textlength(label, font=f24)
        vw  = draw.textlength(value, font=f38)
        draw.text((cx - lw2 / 2, STAT_Y + 10), label, font=f24, fill=_MUTED)
        draw.text((cx - vw / 2,  STAT_Y + 46), value,  font=f38, fill=vcol)
        if i > 0:
            draw.line([(i * col_w, STAT_Y + 16), (i * col_w, STAT_Y + STAT_H - 16)],
                      fill=_DIVIDER, width=1)

    # ════════════════════════════════════════════════════════════════════════
    # 4. 主内容：左栏（核心功能）+ 右栏（项目亮点 + 适用人群）
    # ════════════════════════════════════════════════════════════════════════
    BODY_Y = STAT_Y + STAT_H
    FOOT_H = 64
    BODY_H = CARD_H - BODY_Y - FOOT_H

    LEFT_W = 400
    RIGHT_W = CARD_W - LEFT_W

    # 左栏背景
    draw.rectangle([(0, BODY_Y), (LEFT_W, CARD_H - FOOT_H)], fill=_LCREAM)
    # 右栏背景
    draw.rectangle([(LEFT_W, BODY_Y), (CARD_W, CARD_H - FOOT_H)], fill=_DARK)

    # ── 左栏内容 ─────────────────────────────────────────────────────────────
    LPAD = 28
    y = BODY_Y + 28
    # 节标题
    draw.text((LPAD, y), "⚡ 核心功能", font=f28, fill=_GOLD)
    y += 44

    for hl in highlights[:5]:
        bullet = "◆ "
        bw3 = draw.textlength(bullet, font=f28)
        lines = _wrap(hl, f28, LEFT_W - LPAD * 2 - int(bw3), draw)
        for j, line in enumerate(lines[:3]):
            if j == 0:
                draw.text((LPAD, y), bullet, font=f28, fill=_GOLD)
                draw.text((LPAD + int(bw3), y), line, font=f28, fill=_DTEXT)
            else:
                draw.text((LPAD + int(bw3), y), line, font=f28, fill=_DTEXT)
            y += 42
        y += 6

    # ── 右栏内容 ─────────────────────────────────────────────────────────────
    RPAD = 28
    rx   = LEFT_W + RPAD
    rw   = RIGHT_W - RPAD * 2
    ry   = BODY_Y + 28

    # 节标题
    draw.text((rx, ry), "⚙ 项目亮点", font=f28, fill=_GOLD)
    ry += 44

    # 亮点大标题
    for line in _wrap(hl_title, f48, rw, draw)[:2]:
        draw.text((rx, ry), line, font=f48, fill=_WTEXT)
        ry += 62
    ry += 8

    # 摘要正文
    for line in _wrap(summary, f28, rw, draw)[:9]:
        draw.text((rx, ry), line, font=f28, fill=_WTEXT)
        ry += 40
    ry += 16

    # 适用人群
    draw.text((rx, ry), "👥 适用人群", font=f28, fill=_GOLD)
    ry += 40
    for line in _wrap(audience_text, f28, rw, draw)[:3]:
        draw.text((rx, ry), line, font=f28, fill=_WTEXT)
        ry += 40

    # ════════════════════════════════════════════════════════════════════════
    # 5. 底部版权行
    # ════════════════════════════════════════════════════════════════════════
    FOOT_Y = CARD_H - FOOT_H
    draw.line([(0, FOOT_Y), (CARD_W, FOOT_Y)], fill=_DIVIDER, width=1)
    # repo 路径
    repo = source_url.replace("https://github.com/", "⑂ ")
    draw.text((PAD, FOOT_Y + 18), repo[:48], font=f24, fill=_MUTED)
    # 小红书水印
    wm = "小红书号: AI知识日报"
    wm_w = draw.textlength(wm, font=f24)
    draw.text((CARD_W - PAD - wm_w, FOOT_Y + 18), wm, font=f24, fill=_MUTED)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    logger.info("卡片已生成: %s", output_path)
    return output_path

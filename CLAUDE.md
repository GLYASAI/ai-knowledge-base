# CLAUDE.md — AI 知识库助手项目规范

自动化采集 GitHub Trending 和 Hacker News 的 AI/LLM/Agent 领域技术动态，经 AI 分析后结构化存储，支持多渠道分发（Telegram/飞书）。

## 技术栈

- 语言: Python 3.12
- 工作流: LangGraph
- 部署: OpenClaw
- 依赖管理: pip + requirements.txt

## 编码规范

- 遵循 PEP 8 规范
- 变量命名: snake_case，类名: PascalCase
- 所有函数必须有 docstring（Google 风格）
- 禁止裸 print()，使用 logging 或写入文件
- 禁止 import *
- 文件编码统一 UTF-8

## 项目结构

```
ai-knowledge-base/
├── CLAUDE.md                  — 项目规范（本文件）
├── .claude/settings.json      — Claude Code hooks 配置
├── hooks/                     — 校验脚本
│   ├── validate_json.py       — JSON 知识条目校验器
│   └── validate_article.sh    — PostToolUse hook wrapper
├── knowledge/
│   ├── raw/                   — 原始采集数据（JSON）
│   └── articles/              — 结构化知识条目（JSON）
├── pipeline/                  — 自动化流水线
├── workflows/                 — LangGraph 工作流
└── openclaw/                  — OpenClaw 部署配置
```

## 知识条目格式

每条知识以 JSON 文件存储在 `knowledge/articles/` 目录下：

```json
{
  "id": "github-20260301-001",
  "title": "OpenClaw: 开源 AI Agent 运行时",
  "source_url": "https://github.com/example/project",
  "collected_at": "2026-03-01T10:00:00Z",
  "summary": "一句话中文摘要（不超过 100 字）",
  "analysis": {
    "tech_highlights": ["多 Agent 路由", "50+ 平台支持"],
    "relevance_score": 9,
    "audience": "intermediate"
  },
  "tags": ["agent", "runtime", "open-source"],
  "status": "draft"
}
```

**必填字段**：id, title, source_url, summary, tags, status

**ID 格式**：`{source}-{YYYYMMDD}-{NNN}`（如 `github-20260317-001`）

**status 可选值**：draft / review / published / archived

**audience 可选值**：beginner / intermediate / advanced

**relevance_score**：1-10 分（9-10 改变格局，7-8 直接有帮助，5-6 值得了解）

## 内容规范

- 摘要语言: 中文
- 摘要长度: 20-100 字
- 技术术语保留英文原文（如 LangGraph、Agent、Token）
- tags 至少 1 个

## Hooks

写入或编辑 `knowledge/articles/*.json` 时会自动触发校验（PostToolUse hook）。校验失败会返回错误信息，需根据提示修复后重新写入。

## 红线

- 不编造不存在的项目或数据
- 不在日志中输出 API Key 或敏感信息
- 不执行 rm -rf 等危险命令
- 不修改 CLAUDE.md 本身（除非明确要求）

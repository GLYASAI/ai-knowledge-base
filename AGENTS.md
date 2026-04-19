# AGENTS.md — AI 知识库助手项目规范

自动化采集 GitHub Trending 和 Hacker News 的 AI/LLM/Agent 领域技术动态，经 AI 分析后结构化存储，支持多渠道分发（Telegram/飞书）。

## 技术栈

- 语言: Python 3.12
- AI 编排: OpenCode + 大模型
- 工作流: LangGraph
- 部署: OpenClaw
- 依赖管理: pip + requirements.txt
- 版本控制: Git

## 编码规范

- 遵循 PEP 8 规范
- 变量命名: snake_case
- 类名: PascalCase
- 所有函数必须有 docstring（Google 风格）
- 禁止裸 print()，使用 logging 或写入文件
- 禁止 import *
- 文件编码统一 UTF-8

## 项目结构
```
ai-knowledge-base/
├── AGENTS.md                  — 项目规范（本文件）
├── opencode.json              — OpenCode 配置
├── .opencode/
│   ├── agents/                — Agent 角色定义文件
│   │   ├── collector.md
│   │   ├── analyzer.md
│   │   └── organizer.md
│   └── skills/                — 可复用技能包
│       ├── github-trending/SKILL.md
│       └── tech-summary/SKILL.md
├── knowledge/
│   ├── raw/                   — 原始采集数据（JSON）
│   └── articles/              — 结构化知识条目（JSON）
├── pipeline/                  — 自动化流水线（Week 2）
├── workflows/                 — LangGraph 工作流（Week 3）
└── openclaw/                  — OpenClaw 部署配置（Week 4）
```

## 内容规范

- 摘要语言: 中文
- 摘要长度: 不超过 100 字
- 技术术语保留英文原文（如 LangGraph、Agent、Token）
- 评分标准: 1-10 分，9-10 改变格局，7-8 直接有帮助，5-6 值得了解

## 知识条目格式

每条知识以 JSON 文件存储在 `knowledge/articles/` 目录下：

```json
{
  "id": "2026-03-01-github-openclaw",
  "title": "OpenClaw: 开源 AI Agent 运行时",
  "source": "github-trending",
  "source_url": "https://github.com/example/project",
  "collected_at": "2026-03-01T10:00:00Z",
  "summary": "一句话中文摘要（不超过 100 字）",
  "analysis": {
    "tech_highlights": ["多 Agent 路由", "50+ 平台支持"],
    "relevance_score": 9
  },
  "tags": ["agent", "runtime", "open-source"],
  "status": "draft"
}
```

**必填字段**：id, title, source_url, summary, tags, status
**status 可选值**：draft / reviewed / published

## Agent 角色概览

| 角色 | 文件 | 职责 |
|------|------|------|
| Collector Agent | .opencode/agents/collector.md | 从外部源采集技术动态 |
| Analyzer Agent | .opencode/agents/analyzer.md | 深度分析和价值评估 |
| Organizer Agent | .opencode/agents/organizer.md | 去重、格式化、归档 |

## 红线（绝对禁止）

- 不编造不存在的项目或数据
- 不在日志中输出 API Key 或敏感信息
- 不执行 rm -rf 等危险命令
- 不修改 AGENTS.md 本身（除非明确要求）
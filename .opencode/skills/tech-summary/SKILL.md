---
name: tech-summary
description: 当需要对采集的技术内容进行深度分析总结时使用此技能
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# 技术内容深度分析技能

## 使用场景

对 `knowledge/raw/` 中已采集的技术文章或开源项目进行深度分析，生成结构化摘要、评分和趋势洞察。

## 执行步骤

### 第 1 步：读取最新采集文件

从 `knowledge/raw/` 读取最新的 JSON 文件，提取每个条目的：
- `name` — 项目名
- `url` — 链接（用于获取补充上下文）
- `summary` — 采集阶段的初步摘要
- `stars` — Star 数
- `language` — 编程语言
- `topics` — 标签

如果条目的 `url` 指向 GitHub 仓库，通过 API 获取 README 前 500 字作为补充上下文：

```bash
curl -s -H "Accept: application/vnd.github.v3+json" \
  "https://api.github.com/repos/{owner}/{repo}/readme" | python3 -c "
import json, sys, base64
data = json.load(sys.stdin)
print(base64.b64decode(data['content']).decode('utf-8')[:500])
"
```

获取失败不阻塞流程，基于已有信息继续分析。

### 第 2 步：逐条深度分析

对每个条目独立生成以下 4 项内容：

**1) 中文摘要（不超过 50 字）**

- 公式：做什么 + 怎么做/有什么不同
- 第一句直接点明核心，禁止"本项目是..."等模板开头
- 技术术语保留英文原文（如 RAG、MCP、Agent）
- 用具体事实替代空洞形容词，有数字优先用数字

**2) 技术亮点（2-3 个，用事实说话）**

每个亮点必须包含具体信息，禁止泛泛而谈。

好的示例：
- "自愈机制：运行时自动生成缺失工具函数"
- "双层脱敏：本地 Ollama LLM 语义识别 + 正则兜底"

差的示例：
- "采用了先进的 AI 技术"
- "性能表现优秀"

**3) 评分（1-10 分，附理由）**

评分标准：

| 分数段 | 含义 | 典型特征 |
|--------|------|----------|
| 9-10 | 改变格局 | 定义新品类、突破性架构、千 Star 级爆发 |
| 7-8 | 直接有帮助 | 可集成到现有工作流、解决明确痛点 |
| 5-6 | 值得了解 | 有启发但需大量适配、偏学术或早期 |
| 1-4 | 可略过 | 重复造轮子、噱头大于实质 |

**约束：15 个项目中 9-10 分不超过 2 个。**

评分理由必须是一句具体的判断，说明"为什么给这个分"。

**4) 标签建议（3-5 个）**

优先使用标准词库：
- 领域：`large-language-model`, `agent-framework`, `rag`, `mcp`, `fine-tuning`, `prompt-engineering`, `multi-agent`, `code-generation`
- 技术：`transformer`, `attention`, `embedding`, `vector-database`, `knowledge-graph`
- 工具：`langchain`, `llamaindex`, `openai`, `anthropic`, `deepseek`, `huggingface`
- 场景：`chatbot`, `code-assistant`, `data-analysis`, `document-qa`, `workflow-automation`

词库中没有的概念可新增，必须遵循小写连字符格式。

### 第 3 步：趋势发现

分析全部条目后，提炼本批次的趋势洞察：

- **共同主题**：多个项目指向的同一方向（如"Agent 工具链成熟"）
- **新概念/新术语**：首次出现或快速升温的技术概念
- **信号判断**：这些趋势对 AI 工程师意味着什么

输出 2-4 条趋势，每条包含方向、代表项目和一句话信号描述。

### 第 4 步：输出分析结果 JSON

路径：`knowledge/raw/tech-summary-{YYYY-MM-DD}.json`

```json
{
  "source": "tech-summary",
  "skill": "tech-summary",
  "analyzed_at": "2026-04-19T00:00:00Z",
  "input_file": "github-trending-2026-04-19.json",
  "items": [
    {
      "name": "owner/repo",
      "url": "https://github.com/owner/repo",
      "summary": "不超过 50 字的中文摘要",
      "tech_highlights": ["亮点1：具体事实", "亮点2：具体事实"],
      "score": 8,
      "score_reason": "一句话评分理由",
      "tags": ["agent-framework", "python"]
    }
  ],
  "trends": [
    {
      "direction": "趋势方向",
      "projects": ["project-a", "project-b"],
      "signal": "一句话信号描述"
    }
  ]
}
```

## 注意事项

- 逐条独立评分，不因前面的条目分数高就压低后面的（绝对评分，非相对排名）
- 15 个项目中 9-10 分不超过 2 个
- 摘要必须是中文，不超过 50 字
- 不编造不存在的项目或数据
- 处理完所有条目后再统一输出

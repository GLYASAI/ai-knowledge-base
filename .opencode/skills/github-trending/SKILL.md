---
name: github-trending
description: 当需要采集 GitHub 热门开源项目时使用此技能。适用于知识库采集阶段。
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# GitHub 热门项目采集技能

## 使用场景

在知识库采集阶段，从 GitHub 搜索并采集 AI 领域热门开源项目。

## 执行步骤

### 第 1 步：搜索热门仓库

**必须使用 curl 调用 GitHub API**，WebFetch 不支持 JSON 响应。

```bash
curl -s "https://api.github.com/search/repositories?q=created:>{7天前日期}+stars:>100&sort=stars&order=desc&per_page=30"
```

示例（最近 7 天）：

```bash
curl -s "https://api.github.com/search/repositories?q=AI+OR+LLM+OR+agent+OR+RAG+created:>2026-04-12&sort=stars&order=desc&per_page=30"
```

**请求头**：
```
Accept: application/vnd.github.v3+json
Authorization: Bearer ${GITHUB_TOKEN}
```

### 第 2 步：提取仓库信息

提取 name, full_name, html_url, description, stargazers_count, language, topics

### 第 3 步：过滤

纳入：AI/ML/LLM/Agent 相关、开发者工具、框架重大更新
排除：Awesome 列表、纯教程、Star 刷量、无 README

### 第 4 步：去重

按 full_name 去重，只保留一条

### 第 5 步：撰写中文摘要

公式：[项目名] + 做什么 + 为什么值得关注

### 第 6 步：排序取 Top 15

按 Star 数降序排列

### 第 7 步：输出 JSON

路径：knowledge/raw/github-trending-{YYYY-MM-DD}.json
JSON结构包含：source, skill, collected_at, items数组(name, url, summary, stars, language, topics)

## 注意事项

- GitHub API 未认证限频 10 次/分钟
- 摘要必须是中文
- 不编造不存在的仓库

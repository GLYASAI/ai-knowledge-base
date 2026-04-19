---
name: github-trending
description: 当需要采集 GitHub 热门开源项目时使用此技能。适用于知识库采集阶段。
allowed-tools:
  - Read
  - Grep
  - Glob
  - WebFetch
---

# GitHub Trending 采集技能

## 使用场景

在知识库采集阶段，从 GitHub 搜索并采集 AI 领域热门开源项目。

## 执行步骤

### 步骤 1：搜索热门仓库

GET https://api.github.com/search/repositories?q=created:>{7天前日期}+stars:>100&sort=stars&order=desc&per_page=30

### 步骤 2：提取项目信息

提取 name, full_name, html_url, description, stargazers_count, language, topics

### 步骤 3：过滤

纳入：AI/ML/LLM/Agent 相关、开发者工具、框架重大更新
排除：Awesome 列表、纯教程、Star 刷量、无 README

### 步骤 4：去重

按 full_name 去重，只保留一条。

### 步骤 5：撰写中文摘要

公式：[项目名] + 做什么 + 为什么值得关注

### 步骤 6：排序取 Top 15

按 Star 数降序排列。

### 步骤 7：输出 JSON

路径：knowledge/raw/github-trending-{YYYY-MM-DD}.json

## 注意事项

- GitHub API 未认证限频 10 次/分钟
- 摘要必须是中文
- 不编造不存在的仓库

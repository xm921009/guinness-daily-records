# 吉尼斯世界纪录 · 每日新增追踪

一个 GitHub Actions 定时任务：每天抓取[吉尼斯世界纪录官网](https://www.guinnessworldrecords.com/)公开 sitemap，自动汇总纪录大全，并列出相比前一天**新增 / 更新的纪录**。

<!-- LATEST_START -->
## 最新状态（2026-11-19）

- 首次运行已建立基线：收录 10754 条纪录。
- 从明天起每日对比官网 sitemap，自动列出新增/更新纪录。
<!-- LATEST_END -->

## 它做什么

- **数据源**：吉尼斯官网新闻纪录 sitemap（`sitemap-news-index.xml` 按年份展开到新闻页），每条含官方 URL 与 `lastmod` 时间戳。吉尼斯的新纪录均以新闻形式发布，因此用新闻 sitemap 能敏锐反映纪录动态。纯公开数据，无需 API Key。
- **每日更新**：脚本每天跑一次，把当前 sitemap 与仓库内 `data/seen_url.json`（上次快照）对比：
  - URL 首次出现 → **今日新增纪录**
  - `lastmod` 变新 → **今日更新纪录**
  - 首次运行建立基线，不误报新增。
- **读失败 ≠ 读到空**：抓取失败重试 3 次；有效记录数低于阈值直接失败退出，绝不用残缺数据生成结论。

## 产物

| 文件 | 内容 |
| --- | --- |
| `NEW_RECORDS.md` | 最新一期新增 / 更新纪录（带官方链接） |
| `RECORDS.md` | 纪录大全表格（最近 2000 条，全量在 JSON） |
| `data/latest.json` | 本期结构化结果（新增 / 更新明细） |
| `data/all_records.json` | 全量纪录快照 |
| `data/seen_url.json` | 已见 URL + lastmod，作为下次 diff 基准 |
| `history/YYYY-MM.md` | 按月的每日历史 |

## 口径说明

- 「新增 / 更新」指**官网 sitemap 层面的变化**（新 URL 出现或页面 lastmod 更新），不等于吉尼斯官方正式颁证日期；官网新闻站点更新频繁，能较敏锐反映新纪录动态。
- 所有时间按**北京时间（UTC+8）**显示。GitHub Actions 定时为 UTC 每天 00:30（≈ 北京 08:30），云端调度可能有延迟。
- 本仓库数据来自吉尼斯官网公开 sitemap，仅作个人查询用途，版权归 Guinness World Records 所有。

## 手动触发

Actions 页面 → `每日查询吉尼斯世界纪录` → Run workflow。

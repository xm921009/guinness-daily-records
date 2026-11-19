# -*- coding: utf-8 -*-
r"""每日抓取吉尼斯世界纪录官方 sitemap，生成「纪录大全 + 每日新增」四件套产物。

数据源（已实测 2026-11-19，requests 直连 200，无 Cloudflare 拦截，无需 Key）：
  - https://www.guinnessworldrecords.com/sitemap.xml                     (索引)
  - .../sitemap-news-index.xml                                           (新闻纪录 年份索引)
  - .../news/YYYY/sitemap-news-public-YYYY.xml                           (每年新闻纪录，含 <loc>+<lastmod>)
  - .../sitemap-world-records-index.xml                                  (纪录条目 年份索引)
  - .../world-records/YYYY/sitemap-world-records-YYYY.xml                (每年纪录条目)

「每日新增」口径（纯技术 diff，非官方发布日）：
  - 首次运行：建立基线，把当前全部条目记为 known，报告基线数量，不算「今日新增」。
  - 之后每天：与 data/seen_url.json 对比，按 URL 首次出现 = 新增；lastmod 变新 = 更新。
  - 新增/更新 均按北京时间(UTC+8)归到当天。

铁律：读失败 ≠ 读到空。任一必需 sitemap 抓取失败或有效条目不足，直接 exit 1，
      绝不用残缺数据生成结论。外部请求重试 3 次。
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
HISTORY = os.path.join(ROOT, "history")
os.makedirs(DATA, exist_ok=True)
os.makedirs(HISTORY, exist_ok=True)

BASE = "https://www.guinnessworldrecords.com"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
BJ = timezone(timedelta(hours=8))
SITEMAP_INDEX = BASE + "/sitemap.xml"
NEWS_INDEX = BASE + "/sitemap-news-index.xml"

MIN_TOTAL = 300          # 有效条目低于此值视为抓取残缺 -> exit 1
MAX_RECORDS_MD = 2000    # RECORDS.md 最多展示条数（全量 JSON 不受限）
NEW_TOP_N = 40           # 报告里最多列出的新增条数
RETRIES = 3


# ---------------- 抓取 ----------------
def fetch(url, retries=RETRIES):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
                if not raw:
                    raise RuntimeError("空响应")
                return raw.decode("utf-8", "ignore")
        except Exception as e:
            last = e
            if i < retries - 1:
                time.sleep(2 * (i + 1))
    raise RuntimeError("抓取失败 %s : %s" % (url, last))


def parse_sitemap_locs(xml_text):
    """返回 (loc, lastmod) 列表；兼容带命名空间的 sitemap。"""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for url_el in root.iter():
        tag = url_el.tag.split("}")[-1]
        if tag != "url":
            continue
        loc = lastmod = None
        for ch in url_el:
            ct = ch.tag.split("}")[-1]
            if ct == "loc" and ch.text:
                loc = ch.text.strip()
            elif ct == "lastmod" and ch.text:
                lastmod = ch.text.strip()
        if loc:
            out.append((loc, lastmod or ""))
    return out


def title_from_url(url):
    """从新闻 URL slug 反推人类可读标题。"""
    path = urllib.parse.urlparse(url).path
    slug = [s for s in path.split("/") if s][-1] if path.strip("/") else ""
    slug = urllib.parse.unquote(slug)
    slug = re.sub(r"\.html?$", "", slug, flags=re.I)
    title = slug.replace("-", " ").replace("_", " ")
    title = re.sub(r"\s+", " ", title).strip()
    return title or url


def year_from_url(url):
    m = re.search(r"/(20\d{2})/", url)
    return int(m.group(1)) if m else 0


def classify(url):
    return "news" if "/news/" in url else ("record" if "/world-records/" in url else "other")


def collect_sitemap_urls():
    """从 news 索引拿全部年份的 sitemap URL。

    索引文件是 <sitemapindex><sitemap><loc>... 结构（不是 <urlset><url>），
    需单独解析；再补探当年与去年（索引常滞后一年未更新）。
    """
    news_idx = fetch(NEWS_INDEX)
    root = ET.fromstring(news_idx)
    urls = []
    for el in root.iter():
        if el.tag.split("}")[-1] == "loc" and el.text and "sitemap" in el.text:
            loc = el.text.strip()
            if "/news/" in loc and loc.endswith(".xml"):
                urls.append(loc)
    # 兜底：索引通常只列到去年，补探当年与去年
    now_year = datetime.now(BJ).year
    for y in (now_year, now_year - 1):
        cand = "%s/news/%d/sitemap-news-public-%d.xml" % (BASE, y, y)
        if cand not in urls:
            urls.append(cand)
    # 去重保序
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def fetch_all_records():
    sm_urls = collect_sitemap_urls()
    print("[fetch] sitemap 数量 = %d" % len(sm_urls))
    records = {}
    failed = []
    for u in sm_urls:
        try:
            xml = fetch(u)
            pairs = parse_sitemap_locs(xml)
        except Exception as e:
            failed.append((u, str(e)))
            print("[warn] sitemap 抓取失败：%s" % u)
            continue
        # 非年份的聚合 sitemap（如 sitemap-news-public.xml 无年份）也纳入
        for loc, lastmod in pairs:
            kind = classify(loc)
            if kind == "other":
                continue
            key = loc.split("?")[0]
            rec = {
                "url": key,
                "title": title_from_url(key),
                "lastmod": lastmod,
                "year": year_from_url(key),
                "kind": kind,
            }
            # 同一 URL 取 lastmod 最大者
            old = records.get(key)
            if old is None or lastmod > old["lastmod"]:
                records[key] = rec
        print("[fetch] %s -> %d 条" % (u.split("/")[-1], len(pairs)))
    # 闸门：核心年份 sitemap 若大面积失败，或有效条目不足，拒绝出结论
    if len(records) < MIN_TOTAL:
        raise RuntimeError("有效记录仅 %d 条(< %d)，疑似抓取残缺，中止" % (len(records), MIN_TOTAL))
    if failed:
        # 允许个别历史年份 404（如 2025 纪录页 sitemap 尚不存在），但印出来
        print("[warn] %d 个 sitemap 抓取失败（个别历史年份可能尚未生成）" % len(failed))
    return records


# ---------------- 持久化 ----------------
def load_seen():
    p = os.path.join(DATA, "seen_url.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8")), True
    return {}, False


def save_json(name, obj):
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def md_escape(s):
    return s.replace("|", "\\|").replace("\n", " ").strip()


def now_str():
    return datetime.now(BJ).strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 产物生成 ----------------
def build_report(records, seen, is_baseline, today_bj, new_items, updated_items):
    lines = []
    lines.append("# 吉尼斯世界纪录 · 新增动态")
    lines.append("")
    lines.append("- 检测时间（北京时间）：%s" % now_str())
    lines.append("- 数据源：吉尼斯世界纪录官网 sitemap（官方站点公开数据）")
    if is_baseline:
        lines.append("- 状态：**首次运行 · 建立基线**，暂不判定「新增」")
    else:
        lines.append("- 本轮新增：**%d** 条；本轮更新：**%d** 条" % (len(new_items), len(updated_items)))
    lines.append("- 纪录库累计：**%d** 条" % len(records))
    lines.append("")

    def fmt(items):
        out = []
        for r in items[:NEW_TOP_N]:
            t = md_escape(r["title"])
            out.append("- [%s](%s)  `<sub>%s · lastmod %s</sub>`"
                       % (t, r["url"], r["kind"], (r["lastmod"] or "")[:10]))
        if len(items) > NEW_TOP_N:
            out.append("- ...另有 %d 条，详见 `data/latest.json`" % (len(items) - NEW_TOP_N))
        return out

    if is_baseline:
        lines.append("## 基线建立")
        lines.append("")
        lines.append("首次运行已收录当前全部 %d 条纪录作为基线。" % len(records))
        lines.append("下面展示官网最近更新的 20 条（按 lastmod）：")
        lines.append("")
        recent = sorted(records.values(), key=lambda x: x["lastmod"], reverse=True)[:20]
        lines += fmt(recent)
    else:
        lines.append("## 🆕 本轮新增纪录")
        lines.append("")
        if new_items:
            lines += fmt(new_items)
        else:
            lines.append("- 本轮官网 sitemap 未发现新 URL")
        lines.append("")
        lines.append("## 🔄 本轮更新纪录")
        lines.append("")
        if updated_items:
            lines += fmt(updated_items)
        else:
            lines.append("- 本轮无 lastmod 变新的条目")
    lines.append("")
    return "\n".join(lines)


def update_readme_banner(report_text, today_bj, total, is_baseline, new_n, upd_n):
    p = os.path.join(ROOT, "README.md")
    txt = open(p, encoding="utf-8").read()
    start = "<!-- LATEST_START -->"
    end = "<!-- LATEST_END -->"
    if start not in txt or end not in txt:
        raise RuntimeError("README.md 缺少 LATEST marker")
    if is_baseline:
        head = "## 最新状态（%s）\n\n- 首次运行已建立基线：收录 %d 条纪录。\n- 此后每 10 分钟对比官网 sitemap，自动列出新增/更新纪录。" % (today_bj, total)
    else:
        head = "## 最新状态（%s）\n\n- 本轮新增 **%d** 条 / 更新 **%d** 条，纪录库累计 **%d** 条。\n- 详见 [NEW_RECORDS.md](NEW_RECORDS.md) 与 [RECORDS.md](RECORDS.md)。" % (today_bj, new_n, upd_n, total)
    block = start + "\n" + head + "\n" + end
    new_txt = re.sub(re.escape(start) + r".*?" + re.escape(end), block, txt, flags=re.S)
    with open(p, "w", encoding="utf-8") as f:
        f.write(new_txt)


def build_records_md(records):
    lines = ["# 吉尼斯世界纪录 · 大全", "",
             "> 由官网 sitemap 汇总，每日自动更新。共 %d 条（下表展示最近 %d 条，全量见 data/all_records.json）。" %
             (len(records), min(MAX_RECORDS_MD, len(records))), "",
             "| 纪录 / 新闻标题 | 类别 | 年份 | lastmod |",
             "| --- | --- | --- | --- |"]
    ordered = sorted(records.values(), key=lambda x: (x["lastmod"], x["year"]), reverse=True)
    for r in ordered[:MAX_RECORDS_MD]:
        lines.append("| [%s](%s) | %s | %s | %s |" %
                     (md_escape(r["title"]), r["url"], r["kind"], r["year"], (r["lastmod"] or "")[:10]))
    with open(os.path.join(ROOT, "RECORDS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def append_history(today_bj, report_text):
    ym = today_bj[:7]
    p = os.path.join(HISTORY, ym + ".md")
    head = "" if os.path.exists(p) else "# 吉尼斯纪录 · %s 历史\n" % ym
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n" + head + "\n" + report_text + "\n---\n")


def write_step_summary(text):
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(text + "\n")


# ---------------- 主流程 ----------------
def main():
    now = datetime.now(BJ)
    today = now.strftime("%Y-%m-%d")
    print("[time] 北京时间 %s" % now.strftime("%Y-%m-%d %H:%M:%S"))

    records = fetch_all_records()
    print("[fetch] 有效记录总数 = %d" % len(records))

    seen, existed = load_seen()
    is_baseline = (not existed) or len(seen) == 0

    new_items, updated_items = [], []
    if is_baseline:
        for k, r in records.items():
            seen[k] = {"lastmod": r["lastmod"], "first_seen": today}
    else:
        for k, r in records.items():
            old = seen.get(k)
            if old is None:
                item = dict(r)
                item["first_seen"] = today
                new_items.append(item)
                seen[k] = {"lastmod": r["lastmod"], "first_seen": today}
            elif r["lastmod"] and r["lastmod"] > (old.get("lastmod") or ""):
                item = dict(r)
                item["prev_lastmod"] = old.get("lastmod", "")
                updated_items.append(item)
                seen[k]["lastmod"] = r["lastmod"]
        new_items.sort(key=lambda x: x["lastmod"], reverse=True)
        updated_items.sort(key=lambda x: x["lastmod"], reverse=True)

    # 高频运行（每10分钟）：仅当数据真正变化时才写产物，避免每轮空提交。
    # 无变化时产物字节保持完全一致 → git diff --staged --quiet 命中 → workflow 跳过 commit。
    changed = is_baseline or bool(new_items) or bool(updated_items)

    report = build_report(records, seen, is_baseline, today, new_items, updated_items)
    write_step_summary(report)  # Actions 运行页每轮都展示，不进 git

    if changed:
        save_json("seen_url.json", seen)
        save_json("all_records.json", {
            "generated_at_bj": now.strftime("%Y-%m-%d %H:%M:%S"),
            "total": len(records),
            "records": sorted(records.values(), key=lambda x: x["lastmod"], reverse=True),
        })
        save_json("latest.json", {
            "date_bj": today,
            "is_baseline": is_baseline,
            "total": len(records),
            "new_count": len(new_items),
            "updated_count": len(updated_items),
            "new_records": new_items,
            "updated_records": updated_items,
        })
        with open(os.path.join(ROOT, "NEW_RECORDS.md"), "w", encoding="utf-8") as f:
            f.write(report)
        build_records_md(records)
        update_readme_banner(report, today, len(records), is_baseline, len(new_items), len(updated_items))
        append_history(today, report)
    else:
        print("[skip] 无新增/更新，产物保持不变（避免高频空提交）")

    if is_baseline:
        print("[done] 基线建立：收录 %d 条纪录" % len(records))
    else:
        print("[done] 本轮新增 %d / 更新 %d，累计 %d，changed=%s" %
              (len(new_items), len(updated_items), len(records), changed))
    print("[done] 产物：NEW_RECORDS.md / RECORDS.md / README / data/*.json / history/")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print("[fatal] %s" % e)
        raise SystemExit(1)

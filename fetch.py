#!/usr/bin/env python3
"""生成当日一期数据，写回工作区的 data.json（不提交回仓库）。

优先用仓库里人工写的 picks-YYYY-MM-DD.json；没有更新的就调 GitHub Search API
抓近期新项目，条目说明直接取项目简介原文，并在页面上标注来源。

仅用标准库。环境变量 GH_TOKEN 可选；Actions 里由 secrets.GITHUB_TOKEN 提供。
"""
import datetime
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data.json")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""

BLOCK = (
    "nsfw", "adult", "porn", "jailbreak", "uncensored", "vbios",
    "firmware unlock", "coupon", "airdrop farm", "薅羊毛",
)


def log(msg):
    print("[fetch] " + msg, flush=True)


def api(url):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "gh-daily-picks-static-site",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last = e
            code = getattr(e, "code", None)
            wait = 25 if code in (403, 429) else 3 + attempt * 3
            log("请求失败 (%s)，%s 秒后重试 (%d/3)" % (e, wait, attempt + 1))
            time.sleep(wait)
    raise last


def search(query, days=30):
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    q = "created:>%s stars:>=20 %s" % (since, query)
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({
        "q": q, "sort": "stars", "order": "desc", "per_page": 100,
    })
    return api(url).get("items", [])


def blocked(repo):
    text = ((repo.get("full_name") or "") + " " + (repo.get("description") or "")).lower()
    return any(word in text for word in BLOCK)


def to_item(repo):
    stars = int(repo.get("stargazers_count") or 0)
    created = (repo.get("created_at") or "")[:10]
    try:
        age = max((datetime.date.today() - datetime.date.fromisoformat(created)).days, 1)
    except ValueError:
        age = 1
    lic = repo.get("license") or {}
    return {
        "name": repo.get("full_name") or "",
        "tagline": repo.get("description") or "（项目未填写简介）",
        "language": repo.get("language") or "—",
        "stars": stars,
        "velocity": round(stars / age),
        "license": lic.get("spdx_id") or "—",
        "url": repo.get("html_url") or "",
        "why": "",
        "caveat": "",
        "source": "description",
    }


def fetch_auto():
    today = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).date().isoformat()
    week_ago = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    log("抓候选：近期高星")
    first = search("")
    time.sleep(7)  # Search API 未鉴权限流很严，两条查询之间必须隔开
    log("抓候选：近期活跃")
    second = search("pushed:>%s" % week_ago)

    seen_owner = set()
    pool = []
    for repo in first + second:
        owner = (repo.get("full_name") or "").split("/")[0]
        if not owner or owner in seen_owner or blocked(repo):
            continue
        seen_owner.add(owner)
        pool.append(to_item(repo))

    pool.sort(key=lambda x: x["velocity"], reverse=True)
    picks = pool[:10]
    if len(picks) < 5:
        raise RuntimeError("候选不足，只拿到 %d 条" % len(picks))
    log("候选 %d 条，取日增估算前 %d 条" % (len(pool), len(picks)))
    return {"date": today, "mode": "auto", "picks": picks}


def curated_newer_than(updated):
    found = []
    for path in glob.glob(os.path.join(ROOT, "picks-*.json")):
        m = re.search(r"picks-(\d{4}-\d{2}-\d{2})\.json$", path)
        if m and m.group(1) > updated:
            found.append((m.group(1), path))
    if not found:
        return None
    date, path = max(found)
    raw = json.load(open(path, encoding="utf-8"))
    keys = ("name", "tagline", "language", "stars", "velocity", "license", "url", "why", "caveat")
    picks = []
    for p in raw.get("picks", []):
        it = {k: p.get(k) for k in keys}
        it["source"] = "curated"
        picks.append(it)
    issue = {"date": date, "mode": "curated", "picks": picks}
    if "theme" in raw:
        issue["theme"] = raw["theme"].get("label", "专题")
    log("发现人工精选 %s，优先使用" % path)
    return issue


def main():
    data = json.load(open(DATA, encoding="utf-8"))
    updated = data.get("updated", "")
    try:
        issue = curated_newer_than(updated) or fetch_auto()
    except Exception as e:
        log("抓取失败：%s；保留原有 data.json，站点内容不变" % e)
        return 0

    data["issues"] = [i for i in data.get("issues", []) if i.get("date") != issue["date"]]
    data["issues"].append(issue)
    data["issues"].sort(key=lambda i: i.get("date", ""), reverse=True)
    data["latest"] = issue
    data["updated"] = issue["date"]
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    log("写入 data.json：%s · %s · %d 条" % (issue["date"], issue["mode"], len(issue["picks"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

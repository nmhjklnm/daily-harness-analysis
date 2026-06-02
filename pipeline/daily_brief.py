#!/usr/bin/env python3
"""Generate daily feed brief from Miniflux + configurable LLM CLI.

Pulls last N hours of entries from a self-hosted Miniflux instance, feeds
them to an agentic LLM CLI with interests.md as editorial guidance, and
writes a markdown digest.

Usage:
    ./daily_brief.py                      # run for last 24h
    ./daily_brief.py --hours 48           # last 48h
    ./daily_brief.py --dry-run            # just dump entries + prompt, skip LLM
    ./daily_brief.py --out /tmp/test.md   # override output path
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Make dha_config importable when this script is invoked directly from any cwd.
import sys as _sys
import os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import dha_config  # noqa: E402
from dha_config import env, OUTPUT_DIR, branding  # noqa: E402

HERE = Path(__file__).resolve().parent

# DEFAULT_OUT_DIR falls back to dha_config.OUTPUT_DIR when --day-dir not given.
DEFAULT_OUT_DIR = OUTPUT_DIR

SSH_HOST = env("MINIFLUX_SSH_HOST", "")
MINIFLUX_URL = env("MINIFLUX_URL", "http://127.0.0.1:8080")
MINIFLUX_TOKEN = env("MINIFLUX_TOKEN", "")


def fetch_entries(hours: int) -> list[dict]:
    """Pull entries INGESTED in the last `hours` hours.

    When MINIFLUX_SSH_HOST is empty, runs curl locally.
    When set, proxies through SSH: ssh <host> curl ...

    Filters on created_at (when Miniflux first stored the entry), NOT
    published_at. Some feeds — notably RSSHub's huggingface/daily-papers and
    several newsletter sources — stamp pubDate days in the past, so a
    `published_after` window silently drops papers that only just arrived.

    We pre-filter server-side with `changed_after` (a superset of
    created_at >= cutoff) and then keep only entries genuinely CREATED within
    the window.
    """
    if not MINIFLUX_TOKEN:
        print(
            "ERROR: MINIFLUX_TOKEN not set — see .env.example",
            file=sys.stderr,
        )
        sys.exit(1)

    cutoff = int(time.time()) - hours * 3600
    all_entries: list[dict] = []
    offset = 0
    while True:
        url = (
            f"{MINIFLUX_URL}/v1/entries?changed_after={cutoff}"
            f"&limit=250&offset={offset}&order=changed_at&direction=desc"
        )
        if SSH_HOST:
            cmd = ["ssh", SSH_HOST, f"curl -sf -H 'X-Auth-Token: {MINIFLUX_TOKEN}' '{url}'"]
        else:
            cmd = ["curl", "-sf", "-H", f"X-Auth-Token: {MINIFLUX_TOKEN}", url]
        out = subprocess.check_output(cmd, text=True, timeout=60)
        data = json.loads(out)
        batch = data.get("entries", [])
        if not batch:
            break
        all_entries.extend(batch)
        if len(all_entries) >= data.get("total", 0):
            break
        offset += 250

    def created_epoch(e: dict) -> float:
        ts = (e.get("created_at") or "").replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(ts).timestamp()
        except ValueError:
            return 0.0

    return [e for e in all_entries if created_epoch(e) >= cutoff]


def simplify(entries: list[dict]) -> list[dict]:
    """Strip each entry to {id, feed, title, url, desc} with desc trimmed to 400 chars."""
    out = []
    for e in entries:
        desc = re.sub(r"<[^>]+>", " ", e.get("content", ""))
        desc = re.sub(r"\s+", " ", html.unescape(desc)).strip()[:400]
        out.append(
            {
                "id": e["id"],
                "feed": e["feed"]["title"],
                "title": e["title"],
                "url": e["url"],
                "desc": desc,
            }
        )
    return out


def build_prompt(records: list[dict], interests: str, date_str: str) -> str:
    brand = branding()["brand"]
    return f"""你是一位技术编辑，给读者出一份 24h 信息 digest，类似 Hacker News digest / Stratechery weekly。

# 编辑指引 (interests.md)

{interests}

# 输入: {len(records)} 条 24h 内 RSS entries (JSON, 每条含 id/feed/title/url/desc)

```json
{json.dumps(records, ensure_ascii=False)}
```

# 任务

按内容类型分类整理 markdown digest:

- **Papers** — arXiv 论文，每条 `- [Title](url) — 一句话客观描述这篇 paper 做了什么` (用 entry 的 url 字段, 即 arxiv abs URL)
- **Show HN / Open Source** — GitHub Trending + Show HN 项目，每条 `- [Name](url) — 项目做什么`
- **Industry News** — HN front/best 上的行业新闻 + 大厂 blog 文章
- **Blog Posts** — 个人 blog (Simon Willison / Lilian Weng / Eugene Yan / Chip Huyen / Sebastian Raschka 等) + 二级聚合 newsletter
- **GitHub Trending** — 没在 Show HN 出现但 trending 的项目

硬性要求:

1. **每条必带 URL** — 用 entry JSON 里的 `url` 字段。无 URL 不列
2. **每条只描述"它是什么"** — 不写"为什么对你重要"/"和你 X 项目相关"
3. **顶部置一个 `## 今日重点` 段**: 只从 **Papers 和开源 repo** 里挑 3-5 条最有**技术看点**的 (新方法/新发现/值得上手的项目), 格式 `- **[Title](url)** — 一句客观 hook`, 放在所有分类之前。**不要选 industry news** (融资/模型发布/监管这类新闻不进今日重点)。按技术实质挑, **不**按读者研究方向偏好, **不**写"为什么对你重要"。其余条目仍按分类客观罗列, 不在分类里再加 hot take
4. **不要按用户研究方向分桶** — 按上面 5 个内容类型分
5. **跳过明显垂直领域不相关的** (金融预测/医学诊断/立法/农业 等)
6. **跳过重复条目** (同一篇文章在 HN best + HN front 同时出现, 只列一次)
7. **清洗平台前缀** — 标题里去掉 `Show HN: ` / `Ask HN: ` / `Tell HN: ` / `Launch HN: ` 等 HN 平台标签。`[Show HN: Foo](url)` → `[Foo](url)`。读者不需要看到平台来源前缀
8. 总长度 ≤ 200 行 markdown
8. 结构顺序: H1 `# {brand} — {date_str}` → 一行 metadata (输入条数 + 各类型数量) → `## 今日重点` → 5 个分类
9. 真人技术编辑风格, 禁用震惊体/钩子表情/三连短句铺排/buzzword 凑数
10. 真名分类: Show HN 类的子标题可以叫 "Open Source" 或 "Projects", 不要叫 "Show HN" 当分类名

# 输出

仅 markdown body, 不要包 ```markdown``` 块, 不要前后多余文字。
"""


def run_codex(prompt: str) -> str:
    """Pipe prompt to LLM CLI; return last message body."""
    llm_cli = os.environ.get("LLM_CLI", "codex")
    out_file = Path("/tmp/feed_brief_out.md")
    out_file.unlink(missing_ok=True)
    cmd = [
        llm_cli,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--output-last-message",
        str(out_file),
    ]
    result = subprocess.run(
        cmd, input=prompt, text=True, capture_output=True, timeout=600
    )
    if result.returncode != 0:
        print(
            f"{llm_cli} exit={result.returncode}\nstderr: {result.stderr[:2000]}",
            file=sys.stderr,
        )
        sys.exit(1)
    return out_file.read_text()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--hours",
        type=int,
        default=int(env("LOOKBACK_HOURS", "24")),
        help="Look back N hours (default: LOOKBACK_HOURS env or 24)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Dump entries + prompt; skip LLM")
    ap.add_argument("--out", type=Path, default=None, help="Override output path (.md)")
    ap.add_argument("--day-dir", type=Path, default=None,
                    help="Write into layered day-dir (raw.json + digest.md + html/pdf)")
    ap.add_argument("--no-html", action="store_true", help="Skip HTML render step")
    args = ap.parse_args()

    interests_file = HERE / "interests.md"
    if not interests_file.exists():
        print(f"ERROR: {interests_file} missing", file=sys.stderr)
        sys.exit(1)
    interests = interests_file.read_text()

    # REUSE_RAW=1: backfill faithfully from an already-captured 01-raw.json
    # instead of re-fetching Miniflux (avoids window drift + tunnel dependency).
    # Inert for the normal cron path (env unset).
    reuse_raw = (
        os.environ.get("REUSE_RAW") == "1"
        and args.day_dir is not None
        and (args.day_dir / "01-raw.json").exists()
    )
    if reuse_raw:
        records = json.loads((args.day_dir / "01-raw.json").read_text())
        print(f"REUSE_RAW: loaded {len(records)} records from "
              f"{args.day_dir/'01-raw.json'} (skipped fetch)", file=sys.stderr)
    else:
        print(f"Fetching entries (last {args.hours}h)...", file=sys.stderr)
        entries = fetch_entries(args.hours)
        print(f"Got {len(entries)} entries", file=sys.stderr)
        records = simplify(entries)

    date_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    prompt = build_prompt(records, interests, date_str)

    # Layered day-dir mode: write 01-raw.json alongside digest
    if args.day_dir:
        args.day_dir.mkdir(parents=True, exist_ok=True)
        if not reuse_raw:
            (args.day_dir / "01-raw.json").write_text(
                json.dumps(records, ensure_ascii=False, indent=2)
            )
        if args.out is None:
            args.out = args.day_dir / "03-digest.md"

    if args.dry_run:
        print(f"# DRY RUN — prompt size: {len(prompt)} chars, {len(records)} entries")
        print(prompt[:4000])
        print("... [prompt truncated] ...")
        return

    print(f"Calling {os.environ.get('LLM_CLI', 'codex')} (prompt {len(prompt)} chars)...", file=sys.stderr)
    brief = run_codex(prompt)

    out_path = args.out or (DEFAULT_OUT_DIR / f"{date_str}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(brief)
    print(f"Brief saved: {out_path} ({len(brief)} bytes)", file=sys.stderr)
    print(out_path)

    # In day-dir mode, derive 02-filtered.json from the digest (URLs LLM CLI
    # decided to keep, joined with metadata from 01-raw). This is the
    # 'filtered' layer — explicit subset that survived shallow brief.
    if args.day_dir:
        import json as _json
        url_to_record = {r["url"]: r for r in records}
        filtered = []
        current_section = None
        link_re = re.compile(r"^-\s*\[([^\]]+)\]\((https?://[^)]+)\)\s*(?:—\s*(.+))?$")
        section_re = re.compile(r"^##\s+(.+?)\s*$")
        for line in brief.splitlines():
            ms = section_re.match(line)
            if ms:
                current_section = ms.group(1).strip()
                continue
            ml = link_re.match(line)
            if ml:
                title = ml.group(1).strip()
                url = ml.group(2).rstrip(".,);")
                one_liner = (ml.group(3) or "").strip()
                src = url_to_record.get(url)
                filtered.append(
                    {
                        "category": current_section,
                        "title": title,
                        "url": url,
                        "one_liner": one_liner,
                        "raw_id": src.get("id") if src else None,
                        "feed": src.get("feed") if src else None,
                    }
                )
        (args.day_dir / "02-filtered.json").write_text(
            _json.dumps(filtered, ensure_ascii=False, indent=2)
        )
        print(
            f"Filtered layer: {len(filtered)}/{len(records)} kept ({args.day_dir/'02-filtered.json'})",
            file=sys.stderr,
        )

    # Render HTML + PDF companions (Stratechery-style theme)
    if not args.no_html:
        html_path = out_path.with_suffix(".html")
        pdf_path = out_path.with_suffix(".pdf")
        renderer = HERE / "md_to_html.py"
        try:
            subprocess.run(
                [str(renderer), str(out_path), str(html_path), "--pdf", str(pdf_path)],
                check=True, timeout=120,
            )
        except Exception as e:
            print(f"WARN: HTML/PDF render failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

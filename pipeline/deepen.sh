#!/usr/bin/env bash
# Stage 4-5: 03-digest.md → 04-sources/ (LLM CLI agentic read) → 05-deep.md
# LLM CLI agentic mode: reads each URL using built-in web_search/fetch,
# writes a deep daily report with inline citations.
set -uo pipefail

HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=config.sh
source "$HERE/config.sh"

DAY_DIR="${1:-}"
if [[ -z "$DAY_DIR" || ! -d "$DAY_DIR" ]]; then
  echo "Usage: $0 <day-dir, e.g. ./daily/2026-05-28>"
  exit 2
fi
DIGEST="$DAY_DIR/03-digest.md"
SOURCES="$DAY_DIR/04-sources"
DEEP="$DAY_DIR/05-deep.md"
mkdir -p "$SOURCES"

if [[ ! -f "$DIGEST" ]]; then
  echo "ERROR: $DIGEST not found (run daily_brief.py first)"
  exit 1
fi

DATE_STR="$(basename "$DAY_DIR")"

PROMPT=$(cat <<EOF
你是技术编辑, 像真人一样**逐条打开每个链接, 阅读, 思考, 然后写**有深度的 daily report. 不是浅复述, 是基于实际阅读的评论.

# 输入
浅日报 (按 5 类分组列 URL): $DIGEST

# 工作流 (强制按此跑)

对 $DIGEST 中每个 markdown 链接 \`[Title](URL)\`:

1. 用你内置的 web search/fetch 工具 fetch 那个 URL 的实际内容
   - arxiv URL: 拿 abstract + 主要 contribution
   - GitHub: 拿 README 的"项目做什么"+核心特性
   - blog/news: 拿核心论点和 1-2 个关键事实
2. 把抓到的 raw 内容缩写成 100-200 字, 保存到 $SOURCES/\<slug\>.md (slug 用 arxiv-2510.04141 / github-owner-repo / web-<host>-<hash6> 这种命名)
3. 全部读完后, 写 deep daily report 到 $DEEP, 格式:

# $SITE_BRAND — $DATE_STR

Metadata: 一行只写各类型条数. 不写完成率、不写 fetch 失败数 (这是管线内部指标, 不进读者可见正文).

## 今日重点
- **[Title](url)** — 只从 Papers 和开源 repo 里挑 3-5 条最有技术看点的 (新方法/新发现/值得上手的项目), 一句客观 hook, 用 inline 链接. 不要选 industry news (融资/模型发布/监管不进今日重点). 按技术实质挑, 不按读者研究方向, 不写"为什么对你重要". 放在所有分类之前.

## Papers
- [Title][^1] — 2-4 句, "Daily News" 体感: (1) 这条实际做了什么 / 贡献了什么 (基于真读到的 abstract, 不是猜); (2) 一个具体的关键事实或数字 (来自原文, 如数据集规模/提升幅度/基线对比/核心机制); (3) 为什么值得一看, 客观陈述不写"对你重要".
- ...

## Open Source / Projects
- ...

## Industry News
- ...

## Blog Posts
- ...

## GitHub Trending
- ...

## References

[^1]: Title. arXiv:2510.04141. https://arxiv.org/abs/2510.04141
[^2]: ...

# 硬性要求

- 每条都要 inline footnote 引用 [^N], 末尾完整 References 列表
- 引用编号在文中第一次出现时分配, References 按出现顺序排
- 客观陈述, 不写"对你重要"
- 论文不要复述 title 改述, 要说"做法/贡献/局限"
- 项目要说"做什么+怎么做的"
- 普通条目目标 2-4 句 = 做了什么/贡献 + 一个来自原文的具体关键事实或数字 + 为什么值得一看 (客观), 比之前更有信息密度的 "Daily News" 体感
- 不删条目 (digest 里的全都要进 deep); 信息实在太少的条目允许压到 1-2 句, 但不要为了省事整段都写短
- 总长度 700-1500 行 markdown 可接受 (这是深度版, 比 digest 长; 正常条目写到位优先于压行数)

# 重要

- fetch 失败的 URL (付费墙/403/超时/死链等): **直接用 \$DIGEST 里该条已有的原文描述当正文写**, 作为正常内容输出. 绝不标 \`[FETCH FAILED]\`, 绝不写"未能核验/无法读取/不补充"之类报错或道歉元话语, 也不瞎编没读到的细节. fetch 是锦上添花的深度扩充, 失败就静默回退到原文, 读者不该看到任何失败痕迹.
- 不需要修改 $DIGEST
- 完成后写入 $DEEP 并结束
EOF
)

echo "=== running codex deepen on $DAY_DIR ==="
echo "digest: $DIGEST"
echo "sources dir: $SOURCES"
echo "out: $DEEP"
echo

# Use an ephemeral workspace so codex 's session metadata (.codex/, .agents/,
# auto-init .git) lands in /tmp, not in the user-visible day-dir.
SCRATCH=$(mktemp -d /tmp/feed-deepen.XXXXXX)
"$LLM_CLI" exec \
  --skip-git-repo-check \
  --ephemeral \
  -s workspace-write \
  --add-dir "$DAY_DIR" \
  -C "$SCRATCH" \
  --output-last-message "$SCRATCH/.codex-last.txt" \
  "$PROMPT"

rc=$?
rm -rf "$SCRATCH"
echo
if [[ $rc -ne 0 ]]; then
  echo "codex exited with $rc"
  exit $rc
fi

if [[ -f "$DEEP" ]]; then
  lines=$(wc -l < "$DEEP")
  echo "OK. $DEEP ($lines lines)"
else
  echo "WARN: $DEEP not created by codex"
  exit 1
fi

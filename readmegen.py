#!/usr/bin/env python3
"""readmegen — 从仓库真实结构生成 README 草稿。

只读扫描目标仓库（目录树、语言分布、入口点、文件头注释），
把这些事实喂给 LLM，由它起草一份 README。
永远只生成草稿：模型可能编造细节，TODO 标记 + 人工复核是必须的。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

VERSION = "0.1.0"
MAX_CONTEXT_CHARS = 6000          # 喂给模型的上下文上限
MAX_DEPTH = 4                     # 目录树最大深度
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "dist", "build", ".idea", ".vscode", ".tox", ".mypy_cache",
             ".pytest_cache", "target", ".next"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}

# 扩展名 -> 语言名
LANG_OF = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".jsx": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".c": "C", ".h": "C/C++", ".cpp": "C++", ".hpp": "C++",
    ".rb": "Ruby", ".php": "PHP", ".swift": "Swift", ".cs": "C#",
    ".sh": "Shell", ".bash": "Shell", ".sql": "SQL",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".vue": "Vue", ".svelte": "Svelte",
    ".md": "Markdown", ".rst": "reStructuredText",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".xml": "XML", ".ini": "INI", ".cfg": "INI",
    ".r": "R", ".jl": "Julia", ".lua": "Lua", ".pl": "Perl",
    ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang",
    ".dart": "Dart", ".scala": "Scala", ".hs": "Haskell",
    ".tf": "Terraform", ".dockerfile": "Dockerfile",
}

ENTRY_HINTS = {
    "main.py": "Python 入口",
    "app.py": "Python 入口(候选)",
    "__main__.py": "python -m 入口",
    "index.html": "Web 入口",
    "index.js": "JS 入口(候选)",
    "package.json": "Node 项目(看 scripts)",
    "pyproject.toml": "Python 项目(看 project/scripts)",
    "setup.py": "Python 项目",
    "requirements.txt": "Python 依赖",
    "Dockerfile": "容器化",
    "docker-compose.yml": "多容器编排",
    "Makefile": "Make 任务",
    "go.mod": "Go 项目",
    "Cargo.toml": "Rust 项目",
}


def err(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 1


def walk_tree(root: str, max_depth: int = MAX_DEPTH):
    """返回 (tree_lines, file_paths)。tree_lines 是缩进文本树。"""
    tree_lines: list[str] = []
    file_paths: list[str] = []

    def visit(dirpath: str, depth: int, prefix: str):
        try:
            entries = sorted(os.listdir(dirpath))
        except OSError:
            return
        entries = [e for e in entries if e not in SKIP_FILES]
        dirs = [e for e in entries
                if os.path.isdir(os.path.join(dirpath, e)) and e not in SKIP_DIRS]
        files = [e for e in entries if os.path.isfile(os.path.join(dirpath, e))]
        items = [("d", d) for d in dirs] + [("f", f) for f in files]
        for i, (kind, name) in enumerate(items):
            last = i == len(items) - 1
            branch = "└── " if last else "├── "
            tree_lines.append(f"{prefix}{branch}{name}")
            if kind == "f":
                file_paths.append(os.path.join(dirpath, name))
            elif depth < max_depth:
                visit(os.path.join(dirpath, name), depth + 1,
                      prefix + ("    " if last else "│   "))
            else:
                tree_lines.append(f"{prefix}{'    ' if last else '│   '}└── …(更深层已省略)")

    tree_lines.append(os.path.basename(os.path.abspath(root)) + "/")
    visit(root, 0, "")
    return tree_lines, file_paths


def lang_breakdown(file_paths):
    counts: dict[str, int] = {}
    for fp in file_paths:
        name = os.path.basename(fp)
        if name == "Dockerfile":
            lang = "Dockerfile"
        else:
            ext = os.path.splitext(name)[1].lower()
            lang = LANG_OF.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    total = sum(counts.values()) or 1
    return sorted(((l, c, round(c / total * 100, 1)) for l, c in counts.items()),
                  key=lambda x: -x[1])


def detect_entry_points(root: str, file_paths):
    found = []
    rel = {os.path.relpath(p, root): p for p in file_paths}
    for name, hint in ENTRY_HINTS.items():
        if name in rel:
            detail = hint
            if name == "package.json":
                try:
                    data = json.load(open(rel[name], encoding="utf-8"))
                    scripts = data.get("scripts", {})
                    if scripts:
                        detail += "，scripts: " + ", ".join(
                            f"{k}={v}" for k, v in list(scripts.items())[:6])
                except Exception:
                    pass
            elif name == "pyproject.toml":
                try:
                    text = open(rel[name], encoding="utf-8").read()
                    m = re.search(r"\[project\.scripts\](.*?)(?=\n\[|\Z)",
                                  text, re.S)
                    if m and m.group(1).strip():
                        detail += "，console scripts: " + \
                            ", ".join(l.strip() for l in
                                      m.group(1).strip().splitlines()[:4])
                except Exception:
                    pass
            found.append((name, detail))
    return found


def sample_file_head(fp: str, limit: int = 8) -> str:
    """取文件头部：Python 取模块 docstring，否则取前几行非空行。"""
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            text = f.read(4000)
    except OSError:
        return ""
    if fp.endswith(".py"):
        m = re.match(r'\s*(?:"""|\'\'\')([\s\S]*?)(?:"""|\'\'\')', text)
        if m:
            doc = " ".join(m.group(1).split())
            return doc[:300]
    lines = [ln.strip() for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith("#!")]
    # 跳过纯注释的 shebang 行之后，取前几行有信息量的
    picked = [ln for ln in lines if not ln.startswith("#")][:3] or lines[:3]
    return " / ".join(picked)[:300]


def collect_profile(root: str, max_depth: int = MAX_DEPTH,
                    max_chars: int = MAX_CONTEXT_CHARS) -> dict:
    if not os.path.isdir(root):
        raise FileNotFoundError(f"目录不存在: {root}")
    tree_lines, file_paths = walk_tree(root, max_depth)
    if not file_paths:
        raise ValueError("目录为空或没有可扫描的文件")

    langs = lang_breakdown(file_paths)
    entries = detect_entry_points(root, file_paths)

    # 采样：优先入口点文件 + 各语言代表文件
    sampled: list[tuple[str, str]] = []
    seen_langs: set[str] = set()
    entry_set = {os.path.relpath(p, root) for _, p in
                 [(n, os.path.join(root, n)) for n in ENTRY_HINTS if os.path.isfile(os.path.join(root, n))]}
    ordered = sorted(file_paths,
                     key=lambda p: (0 if os.path.relpath(p, root) in entry_set else 1,
                                    len(p)))
    for fp in ordered:
        if len(sampled) >= 12:
            break
        ext = os.path.splitext(fp)[1].lower()
        lang = LANG_OF.get(ext, "?")
        if lang != "?" and lang in seen_langs and \
                os.path.relpath(fp, root) not in entry_set:
            continue
        head = sample_file_head(fp)
        if head:
            sampled.append((os.path.relpath(fp, root), head))
            seen_langs.add(lang)

    parts = [
        f"# 仓库: {os.path.basename(os.path.abspath(root))}",
        f"# 文件数: {len(file_paths)}",
        "",
        "## 目录树",
        *tree_lines,
        "",
        "## 语言分布",
        *([f"- {l}: {c} 个文件 ({p}%)" for l, c, p in langs] or ["- (无可识别语言)"]),
        "",
        "## 检测到的入口点",
        *([f"- {n}: {d}" for n, d in entries] or ["- (未检测到明显入口点)"]),
        "",
        "## 文件头采样",
        *[f"- {p}: {h}" for p, h in sampled],
    ]
    profile_text = "\n".join(parts)
    truncated = False
    if len(profile_text) > max_chars:
        profile_text = (profile_text[:max_chars]
                        + "\n\n…（已截断：仓库内容超出约 "
                        + f"{len(profile_text) - max_chars} 字符，"
                        + "后续文件未包含在本次分析中）")
        truncated = True
    return {
        "repo": os.path.basename(os.path.abspath(root)),
        "file_count": len(file_paths),
        "languages": [{"language": l, "files": c, "percent": p}
                      for l, c, p in langs],
        "entry_points": [{"file": n, "detail": d} for n, d in entries],
        "samples": [{"file": p, "head": h} for p, h in sampled],
        "truncated": truncated,
        "profile_text": profile_text,
    }


ZH_SYSTEM = """你是一个技术文档助手。根据用户提供的仓库结构画像（目录树、语言分布、入口点、文件头采样），
为这个仓库起草一份 README.md 草稿。

必须遵守：
1. 只基于画像中的事实写；凡是画像里没有、需要你推测的内容，一律写成 `<!-- TODO: 待人工确认：…… -->` 标记，不许编造具体数字、作者、链接。
2. 输出 Markdown，包含这些章节：项目简介 / 特性 / 安装 / 快速开始 / 配置 / License。
3. 项目简介不超过 3 句话；特性 3-6 条 bullet；安装和快速开始给出可直接复制的命令（从入口点推导，不确定就标 TODO）。
4. 语言：简体中文。
"""

EN_SYSTEM = """You are a technical documentation assistant. Given a repository profile
(directory tree, language breakdown, entry points, file-head samples), draft a README.md.

Rules:
1. Only write from facts in the profile. Anything not in the profile that you'd have to guess
   must be marked `<!-- TODO: needs human confirmation: ... -->`. Never invent numbers, authors, or links.
2. Output Markdown with these sections: Overview / Features / Installation / Quickstart / Configuration / License.
3. Overview <= 3 sentences; Features 3-6 bullets; Installation/Quickstart give copy-pasteable commands
   derived from entry points (mark TODO when unsure).
4. Language: English.
"""

ZH_USER_TMPL = "以下是仓库结构画像（可能已截断）：\n\n{profile}\n\n请据此起草 README.md 草稿。"
EN_USER_TMPL = "Here is the repository profile (may be truncated):\n\n{profile}\n\nDraft the README.md based on it."


def build_messages(profile_text: str, lang: str):
    if lang == "en":
        return [{"role": "system", "content": EN_SYSTEM},
                {"role": "user", "content": EN_USER_TMPL.format(profile=profile_text)}]
    return [{"role": "system", "content": ZH_SYSTEM},
            {"role": "user", "content": ZH_USER_TMPL.format(profile=profile_text)}]


def call_llm(messages, api_key: str, base_url: str, model: str,
             timeout: int = 120) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.4,
    }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"API 请求失败 (HTTP {e.code}): {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络请求失败: {e.reason}")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError(f"API 返回格式异常: {str(data)[:300]}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="readmegen",
        description="从仓库真实结构生成 README 草稿（只读扫描 + LLM 起草）")
    ap.add_argument("path", nargs="?", default=".",
                    help="目标仓库目录（默认当前目录）")
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="把草稿写入文件（默认打印到 stdout）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只展示仓库画像和 prompt，不发网络请求")
    ap.add_argument("--json", action="store_true",
                    help="只输出仓库画像 JSON，不调用 LLM")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh",
                    help="草稿语言（默认 zh）")
    ap.add_argument("--max-depth", type=int, default=MAX_DEPTH,
                    help=f"目录树最大深度（默认 {MAX_DEPTH}）")
    ap.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""),
                    help="API key（默认读 OPENAI_API_KEY）")
    ap.add_argument("--base-url",
                    default=os.environ.get("OPENAI_BASE_URL",
                                           "https://api.openai.com/v1"),
                    help="OpenAI 兼容接口地址")
    ap.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                    help="模型名（默认 gpt-4o-mini，可用 OPENAI_MODEL 覆盖）")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = ap.parse_args(argv)

    try:
        profile = collect_profile(args.path, max_depth=args.max_depth)
    except FileNotFoundError as e:
        return err(str(e))
    except ValueError as e:
        return err(str(e))

    if args.json:
        out = {k: v for k, v in profile.items() if k != "profile_text"}
        out["profile_text"] = profile["profile_text"]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    messages = build_messages(profile["profile_text"], args.lang)

    if args.dry_run:
        print("===== 仓库画像 =====")
        print(profile["profile_text"])
        print("\n===== 发给模型的 prompt（system + user）=====")
        for m in messages:
            print(f"--- {m['role']} ---")
            print(m["content"])
        print("\n(dry-run: 未发送网络请求)")
        return 0

    if not args.api_key:
        return err("未找到 API key。请设置 OPENAI_API_KEY 环境变量，或用 --api-key 传入。")

    print(f"正在为 [{profile['repo']}] 起草 README…（{args.model}）",
          file=sys.stderr)
    try:
        draft = call_llm(messages, args.api_key, args.base_url,
                         args.model, args.timeout)
    except RuntimeError as e:
        return err(str(e))

    header = ("<!-- 本草稿由 readmegen 自动生成，仅供起草参考；"
              "模型可能编造细节，请逐段人工复核后再发布。 -->\n\n")
    draft = header + draft.strip() + "\n"
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(draft)
        except OSError as e:
            return err(f"写入文件失败: {e}")
        print(f"已写入: {args.output}", file=sys.stderr)
    else:
        print(draft)
    return 0


if __name__ == "__main__":
    sys.exit(main())

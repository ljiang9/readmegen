# readmegen

从仓库的**真实结构**生成 README 草稿：只读扫描目录树、语言分布、入口点和文件头注释，把这些事实喂给 LLM，由它起草一份带标准章节的 README。

> **诚实声明**：本 README 为手工撰写，**不是**用 readmegen 自己生成的（开发时只用 `--dry-run` 验证过画像采集逻辑）。工具输出的每一份草稿都只是起点——模型会一本正经地编造细节，所有不确定的地方都会标成 `TODO`，发布前必须人工逐段复核。

## 它解决什么

给老仓库/接手来的仓库补 README 时，最烦的是"从零开始写"。readmegen 帮你做 80% 的机械活：

- 扫出目录树（限深、自动跳过 `.git` / `node_modules` / `__pycache__` 等）
- 按扩展名统计语言分布
- 识别入口点（`main.py`、`__main__.py`、`index.html`、`package.json` scripts、`pyproject.toml` 等）
- 采样文件头部的 docstring / 注释

然后把这份"仓库画像"（默认截断到 ~6000 字符，超了会明确标注）发给 OpenAI 兼容接口，拿回一份包含 **项目简介 / 特性 / 安装 / 快速开始 / 配置 / License** 的草稿。

## 安装

零依赖，Python 3.10+：

```bash
git clone https://github.com/ljiang9/readmegen.git
cd readmegen
python -m readmegen --help
```

## 快速开始

```bash
# 先看看会发给模型什么（不联网）
python -m readmegen --dry-run ./你的仓库

# 只看结构画像 JSON
python -m readmegen --json ./你的仓库

# 真正生成草稿（需要 OPENAI_API_KEY）
export OPENAI_API_KEY=sk-...
python -m readmegen ./你的仓库 -o README.draft.md

# 英文草稿
python -m readmegen --lang en ./你的仓库 -o README.draft.md
```

兼容任何 OpenAI 格式的接口：

```bash
export OPENAI_BASE_URL=https://你的网关/v1
export OPENAI_MODEL=你的模型名
```

## CLI

```
usage: readmegen [-h] [--version] [-o FILE] [--dry-run] [--json]
                 [--lang {zh,en}] [--max-depth N] [--api-key KEY]
                 [--base-url URL] [--model MODEL] [--timeout SECS]
                 [path]
```

| 参数 | 说明 |
|---|---|
| `path` | 目标仓库目录，默认当前目录 |
| `-o FILE` | 草稿写入文件（默认打印到 stdout） |
| `--dry-run` | 只展示画像 + prompt，不发网络请求 |
| `--json` | 只输出画像 JSON |
| `--lang en` | 生成英文草稿（默认中文） |
| `--max-depth` | 目录树深度，默认 4 |

## 诚实的设计

1. **TODO 标记**：prompt 明确要求模型"画像里没有的一律标 TODO，不许编造"。输出里出现 `<!-- TODO: 待人工确认 -->` 是**正常且期望的**，不是 bug。
2. **截断透明**：画像超过 6000 字符会被截断，并在末尾注明超了多少、哪些没包含。
3. **只读扫描**：工具永远不写目标仓库，`-o` 只写你指定的那一个文件。
4. **草稿横幅**：每次输出头部都带"请人工复核"的注释，防止有人直接把草稿当成品发布。

## 已知局限

- 模型输出质量取决于它听不听"标 TODO"的话；遇到喜欢编造的模型，复核要更仔细。
- 入口点识别是启发式的（文件名匹配），怪异的项目结构可能漏检。
- 语言统计只看扩展名，不看实际代码量。
- 非流式输出，长仓库的草稿要等一次返回（默认超时 120 秒）。

## License

MIT — 详见 [LICENSE](LICENSE)。

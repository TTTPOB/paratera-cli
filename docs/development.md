# 开发与质量检查

在项目根目录用现有的 uv 环境安装开发依赖：

```sh
uv sync --group dev
```

激活项目 `.venv`，让 Pyrefly 使用其中的 Python 与依赖，然后安装三个 Git 阶段的 hook（只需安装一次）：

```sh
. .venv/bin/activate
uvx pre-commit install --hook-type pre-commit --hook-type pre-merge-commit --hook-type pre-push
```

提交前主动检查全部文件：

```sh
uvx pre-commit run --all-files
uvx pre-commit run pyrefly-check --hook-stage manual --all-files
```

Ruff 的 `check --fix` 和 `format` 会修改文件；检查后确认差异，再暂存并提交。Pyrefly 使用 `basic` preset，检查 `src/`、`tests/` 与 `scripts/`，以 `src/` 解析项目导入。

#!/bin/bash
# ============================================================
#  ALAS 手动更新脚本 (Manual update for your ALAS fork)
#
#  将上游 master 的最新内容合并进当前分支,并保留你的本地改动。
#  不要使用 GUI 的"更新"按钮,它执行 git reset --hard 会删掉你的改动。
#
#  用法(在 Git Bash 中,仓库根目录):
#      bash update.sh        或   ./update.sh
#  或直接双击 update.bat
# ============================================================
set -e

cd "$(dirname "$0")"

UPSTREAM_BRANCH="master"

echo "=========================================="
echo " ALAS 手动更新"
echo "=========================================="

# ---- 检查 git ----
command -v git >/dev/null 2>&1 || { echo "[错误] 未找到 git"; exit 1; }

# ---- 检查是否在 git 仓库 ----
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "[错误] 当前目录不是 git 仓库"
    exit 1
fi

CURRENT=$(git rev-parse --abbrev-ref HEAD)
echo "当前分支: $CURRENT"
STASHED=0

# ---- 1. 工作区不干净则先暂存 ----
if [ -n "$(git status --porcelain)" ]; then
    echo
    echo "检测到未提交的改动:"
    git status --short
    echo
    read -r -p "是否把这些改动暂存(git stash)后继续? (y/n) " ans
    if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
        git stash push -m "update-$(date +%s)"
        STASHED=1
        echo "已暂存。"
    else
        echo "已取消。请先提交或暂存你的改动。"
        exit 1
    fi
fi

# ---- 2. 更新 master ----
echo
echo "--- [1/4] 更新 $UPSTREAM_BRANCH ---"
if [ "$CURRENT" != "$UPSTREAM_BRANCH" ]; then
    git checkout "$UPSTREAM_BRANCH"
fi
git pull origin "$UPSTREAM_BRANCH"

# ---- 3. 合并进当前分支 ----
if [ "$CURRENT" != "$UPSTREAM_BRANCH" ]; then
    echo
    echo "--- [2/4] 合并 $UPSTREAM_BRANCH 到 $CURRENT ---"
    git checkout "$CURRENT"
    git merge "$UPSTREAM_BRANCH"
else
    echo "已在 $UPSTREAM_BRANCH,无需合并。"
fi

# ---- 4a. 检查合并冲突 ----
if git status --porcelain | grep -qE '^(UU|AA|DD|AU|UA|DU|UD) '; then
    echo
    echo "[!] 合并存在冲突,请手动解决:"
    echo "    git status                    # 查看冲突文件"
    echo "    打开文件,搜索 <<<<<<< 标记,手动合并后删掉标记"
    echo "    然后: git add . && git commit -m \"Merge $UPSTREAM_BRANCH into $CURRENT\""
    echo "    最后再运行: bash update.sh"
    if [ "$STASHED" = "1" ]; then
        echo "提示: 你的改动暂存在 git stash 中,解决冲突后执行 git stash pop 恢复。"
    fi
    exit 1
fi

# ---- 4b. 恢复暂存的改动 ----
if [ "$STASHED" = "1" ]; then
    echo
    echo "--- 恢复暂存的改动 ---"
    git stash pop
    if git status --porcelain | grep -qE '^(UU|AA|DD|AU|UA|DU|UD) '; then
        echo
        echo "[!] 暂存改动与上游有冲突,请手动解决后提交。"
        exit 1
    fi
fi

# ---- 5. 重新生成配置 ----
echo
echo "--- [3/4] 重新生成配置 ---"
if [ -x ./toolkit/python.exe ]; then
    ./toolkit/python.exe -m module.config.config_updater
elif command -v python >/dev/null 2>&1; then
    python -m module.config.config_updater
else
    echo "[警告] 未找到 python,请手动运行:"
    echo "        ./toolkit/python.exe -m module.config.config_updater"
fi

# ---- 6. 提交重新生成的配置文件(只提交这些路径) ----
echo
echo "--- [4/4] 提交生成的配置 ---"
git add config/template.json \
        module/config/config_generated.py \
        module/config/argument/args.json \
        module/config/argument/menu.json \
        module/config/i18n/ 2>/dev/null || true
if ! git diff --cached --quiet; then
    git commit -m "Update generated config after merging $UPSTREAM_BRANCH"
fi

# ---- 完成 ----
echo
echo "=========================================="
echo " 更新完成!"
echo "=========================================="
git log --oneline -3
echo
echo "你的本地改动已保留在分支 $CURRENT。"
echo "如果上方有未提交的其它文件,请自行 git add / git commit。"

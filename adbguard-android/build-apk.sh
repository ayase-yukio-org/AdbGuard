#!/usr/bin/env bash
# ---------------------------------------------------------------
# AdbGuard APK 构建脚本（Windows / Git Bash）
#
# 为什么需要它：
#   本机 C 盘已 100% 占满，而 Gradle 默认把工作目录放在
#   C:\Users\<用户>\.gradle，构建时会 "No space left on device"。
#   这里把 Gradle 工作目录与临时目录都指到 G 盘（243GB 空闲）。
#
# 用法：
#   ./build-apk.sh                    # 打 debug 包
#   ./build-apk.sh :app:assembleRelease
#   ./build-apk.sh clean
# ---------------------------------------------------------------
set -e

export GRADLE_USER_HOME="G:/gradle-home"
export TMPDIR="G:/build-tmp"
# JVM 在 Windows 上读的是 TEMP / TMP（不是 TMPDIR），两个都设上
export TEMP="G:/build-tmp"
export TMP="G:/build-tmp"
mkdir -p "$GRADLE_USER_HOME" "$TMPDIR"

cd "$(dirname "$0")"

if [ $# -eq 0 ]; then
  set -- :app:assembleDebug
fi

echo "GRADLE_USER_HOME = $GRADLE_USER_HOME"
echo "TMPDIR           = $TMPDIR"
echo "TASK             = $*"
echo

./gradlew "$@"

echo
echo "==== 产物 ===="
find app/build/outputs -name "*.apk" -exec ls -lh {} \; 2>/dev/null || echo "(未找到 apk)"

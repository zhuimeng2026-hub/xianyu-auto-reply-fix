#!/bin/bash
# 微信适配脚本 - 一次性执行 wrapper
# 明天 2026-06-22 04:00 执行

LOG="/opt/xianyu-auto-reply-fix/logs/wechat-adapt-cron-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$(dirname "$LOG")"

{
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 开始执行微信适配脚本"
    cd /opt/xianyu-auto-reply-fix
    python3 scripts/wechat-adapt.py --launch 2>&1
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 执行完成，退出码: $EXIT_CODE"

    # 执行完成后自动移除 crontab 中的此任务（一次性）
    crontab -l 2>/dev/null | grep -v 'wechat-adapt-cron.sh' | crontab -
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 已从 crontab 移除本任务"
} >> "$LOG" 2>&1

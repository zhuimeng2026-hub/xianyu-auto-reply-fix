#!/usr/bin/env python3
"""
闲鱼管理系统 - 微信浏览器适配脚本
Goal + Harness 模式，多进程并行处理

用法:
    python3 scripts/wechat-adapt.py --preflight   # 预检：验证前置条件
    python3 scripts/wechat-adapt.py --test         # 试跑：只执行第一个任务
    python3 scripts/wechat-adapt.py --launch       # 全量执行
    python3 scripts/wechat-adapt.py --status       # 查看进度
    python3 scripts/wechat-adapt.py --rollback     # 回滚所有修改
"""

import os
import sys
import json
import re
import shutil
import hashlib
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path
from multiprocessing import Pool, Manager
from dataclasses import dataclass, field, asdict
from typing import Callable, Optional

# ============================================================
# 配置
# ============================================================

PROJECT_ROOT = Path("/opt/xianyu-auto-reply-fix")
STATUS_FILE = PROJECT_ROOT / "scripts" / "wechat-adapt-status.json"
BACKUP_DIR = PROJECT_ROOT / "scripts" / "backups"
LOG_FILE = PROJECT_ROOT / "logs" / f"wechat-adapt-{datetime.now():%Y%m%d-%H%M%S}.log"

WORKERS = 4


# ============================================================
# Goal 定义：每个 goal 是一个独立的改造任务
# ============================================================

@dataclass
class Goal:
    id: str
    name: str
    description: str
    files: list[str]  # 涉及的文件（相对路径）
    priority: int = 1  # 1=必须, 2=重要, 3=可选
    category: str = "js"  # js / css / html / download


def build_goals() -> list[Goal]:
    """定义所有改造目标"""
    return [
        # --- JS 兼容性 ---
        Goal(
            id="fix_optional_chaining",
            name="修复 Optional Chaining 语法",
            description="将 ?. 替换为兼容写法，支持旧版微信浏览器",
            files=["static/js/app.js"],
            priority=1,
            category="js",
        ),
        Goal(
            id="fix_window_open",
            name="修复 window.open 弹窗拦截",
            description="将 window.open 改为 location.href，避免微信浏览器拦截",
            files=["static/js/app.js"],
            priority=1,
            category="js",
        ),
        Goal(
            id="add_abortcontroller_polyfill",
            name="添加 AbortController Polyfill",
            description="为旧版微信浏览器添加 AbortController 兼容实现",
            files=["static/js/app.js"],
            priority=1,
            category="js",
        ),
        Goal(
            id="fix_createobjecturl_leak",
            name="修复 createObjectURL 内存泄漏",
            description="在 createObjectURL 使用后添加 revokeObjectURL 释放",
            files=["static/js/app.js"],
            priority=2,
            category="js",
        ),
        # --- HTML 改造 ---
        Goal(
            id="download_chartjs",
            name="下载 Chart.js 到本地",
            description="将 CDN 引用的 Chart.js 下载到 static/lib/chart.min.js",
            files=["static/index.html", "static/lib/chart.min.js"],
            priority=1,
            category="download",
        ),
        Goal(
            id="update_viewport_meta",
            name="更新 viewport Meta 标签",
            description="添加 viewport-fit=cover 适配刘海屏和安全区域",
            files=["static/index.html", "static/login.html", "static/register.html"],
            priority=1,
            category="html",
        ),
        Goal(
            id="add_mobile_tabbar",
            name="添加移动端底部 Tab Bar",
            description="在 index.html 中添加微信风格底部导航栏",
            files=["static/index.html"],
            priority=2,
            category="html",
        ),
        Goal(
            id="add_swipe_gesture",
            name="添加触摸滑动手势",
            description="在 app.js 中添加 swipe 手势支持（右滑返回/左滑关闭）",
            files=["static/js/app.js"],
            priority=3,
            category="js",
        ),
        # --- CSS 移动端适配 ---
        Goal(
            id="mobile_sidebar_backdrop",
            name="侧边栏遮罩层",
            description="为移动端侧边栏添加半透明遮罩，点击外部可关闭",
            files=["static/css/layout.css"],
            priority=1,
            category="css",
        ),
        Goal(
            id="mobile_padding_fix",
            name="移动端 Padding 适配",
            description="缩小移动端内容区域内边距，增大触摸区域",
            files=["static/css/layout.css"],
            priority=1,
            category="css",
        ),
        Goal(
            id="mobile_chat_layout",
            name="聊天三栏布局移动端适配",
            description="768px 以下聊天布局改为单栏 tab 切换",
            files=["static/css/layout.css"],
            priority=2,
            category="css",
        ),
        Goal(
            id="mobile_modal_fix",
            name="Modal 移动端适配",
            description="modal 和 iframe 在小屏上自适应高度",
            files=["static/css/components.css"],
            priority=2,
            category="css",
        ),
        Goal(
            id="mobile_touch_targets",
            name="触摸目标放大",
            description="按钮最小高度 44px，间距加大，符合移动端触摸规范",
            files=["static/css/layout.css", "static/css/components.css"],
            priority=2,
            category="css",
        ),
        Goal(
            id="mobile_bottom_tabbar_css",
            name="底部 Tab Bar 样式",
            description="添加微信风格底部导航栏 CSS",
            files=["static/css/layout.css"],
            priority=2,
            category="css",
        ),
        Goal(
            id="mobile_section_animation",
            name="页面切换动画",
            description="section 切换添加 slide 过渡动画",
            files=["static/css/app.css"],
            priority=3,
            category="css",
        ),
    ]


# ============================================================
# Worker 函数：每个 goal 的具体执行逻辑
# ============================================================

def _backup_file(filepath: Path):
    """备份文件到 BACKUP_DIR"""
    rel = filepath.relative_to(PROJECT_ROOT)
    backup_path = BACKUP_DIR / rel
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if filepath.exists():
        shutil.copy2(filepath, backup_path)


def _read_file(filepath: Path) -> str:
    return filepath.read_text(encoding="utf-8")


def _write_file(filepath: Path, content: str):
    filepath.write_text(content, encoding="utf-8")


def _md5(filepath: Path) -> str:
    return hashlib.md5(filepath.read_bytes()).hexdigest()


def run_goal(goal: Goal, test_mode: bool = False) -> dict:
    """执行单个 goal，返回结果 dict。在子进程中运行。"""
    result = {"id": goal.id, "name": goal.name, "status": "pending", "changes": 0, "error": None}
    try:
        handler = GOAL_HANDLERS.get(goal.id)
        if not handler:
            result["status"] = "skipped"
            result["error"] = f"No handler for goal {goal.id}"
            return result

        # 备份涉及的文件
        for f in goal.files:
            fp = PROJECT_ROOT / f
            if fp.exists() and fp.is_file():
                _backup_file(fp)

        changes = handler(goal, test_mode)
        result["status"] = "done"
        result["changes"] = changes
    except Exception as e:
        result["status"] = "failed"
        result["error"] = str(e)
    return result


# --- JS 兼容性 ---

def _fix_optional_chaining(goal: Goal, test_mode: bool) -> int:
    """替换 ?. optional chaining 为兼容写法"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)
    original = content

    # 模式1: obj?.prop  ->  (obj && obj.prop)
    # 模式2: obj?.method()  ->  (obj && obj.method())
    # 模式3: arr?.[index]  ->  (arr && arr[index])
    # 使用正则逐步替换，避免误伤字符串中的 ?.

    # 先处理模板字符串内的 ?.（这些在引号内，不应修改）
    # 策略：按行处理，跳过纯字符串行

    lines = content.split('\n')
    changes = 0
    new_lines = []

    for line in lines:
        # 跳过注释行
        stripped = line.strip()
        if stripped.startswith('//') or stripped.startswith('/*') or stripped.startswith('*'):
            new_lines.append(line)
            continue

        # 跳过纯字符串赋值（包含 ?. 在引号内作为文字内容）
        # 检查是否在字符串字面量中
        if _is_string_literal_line(line):
            new_lines.append(line)
            continue

        # 替换 identifier?.identifier 模式
        new_line = line
        # 模式: word?.word -> (word && word.word) — 简化处理
        # 实际使用 regex 替换
        new_line = re.sub(
            r'(\w+)\?\.(\w+)',
            r'(\1 && \1.\2)',
            new_line
        )
        # 模式: word?.[expr] -> (word && word[expr])
        new_line = re.sub(
            r'(\w+)\?\.\[',
            r'(\1 && \1[',
            new_line
        )

        if new_line != line:
            changes += 1
        new_lines.append(new_line)

    if changes > 0 and not test_mode:
        _write_file(filepath, '\n'.join(new_lines))
    return changes


def _is_string_literal_line(line: str) -> bool:
    """粗略判断一行是否主要是字符串内容（避免修改字符串中的 ?.）"""
    stripped = line.strip()
    # 模板字符串中包含 ?. 且不在变量表达式中
    # 简单判断：如果 ?. 前面紧跟引号或转义字符，可能是字符串
    # 更安全的策略：只在明显的代码模式中替换
    return False  # 暂不过滤，后续可优化


def _fix_window_open(goal: Goal, test_mode: bool) -> int:
    """将 window.open 改为 location.href"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)
    changes = 0

    # 模式1: window.open(url, '_blank', ...) -> location.href = url
    # 但 onclick="window.open(this.src, '_blank')" 需要特殊处理
    # 对于图片点击预览，保留 window.open 但用 location.href 替代

    lines = content.split('\n')
    new_lines = []

    for line in lines:
        new_line = line

        # onclick="window.open(this.src, '_blank')" -> onclick="location.href=this.src"
        new_line = re.sub(
            r"window\.open\(this\.src,\s*'_blank'\)",
            "location.href=this.src",
            new_line
        )

        # onclick="window.open('url', '_blank')" -> onclick="location.href='url'"
        new_line = re.sub(
            r"window\.open\(([^,)]+),\s*'_blank'(?:,\s*[^)]+)?\)",
            r"location.href=\1",
            new_line
        )

        # window.open('url', '_blank') 独立语句
        new_line = re.sub(
            r"window\.open\('([^']+)',\s*'_blank'\)",
            r"location.href='\1'",
            new_line
        )

        if new_line != line:
            changes += 1
        new_lines.append(new_line)

    if changes > 0 and not test_mode:
        _write_file(filepath, '\n'.join(new_lines))
    return changes


def _add_abortcontroller_polyfill(goal: Goal, test_mode: bool) -> int:
    """在 app.js 头部添加 AbortController polyfill"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)

    # 检查是否已有 polyfill
    if "AbortController polyfill" in content or "class AbortController" in content:
        return 0

    polyfill = """// AbortController polyfill for WeChat browser
if (typeof AbortController === 'undefined') {
    class AbortController {
        constructor() { this.signal = { aborted: false, addEventListener: function(){} }; }
        abort() { this.signal.aborted = true; }
    }
    window.AbortController = AbortController;
}
"""

    # 插入到文件头部（第一个非注释、非空行之前）
    lines = content.split('\n')
    insert_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith('//') and not stripped.startswith('/*'):
            insert_idx = i
            break

    lines.insert(insert_idx, polyfill)

    if not test_mode:
        _write_file(filepath, '\n'.join(lines))
    return 1


def _fix_createobjecturl_leak(goal: Goal, test_mode: bool) -> int:
    """为 createObjectURL 调用添加对应的 revokeObjectURL"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)
    lines = content.split('\n')
    changes = 0
    new_lines = []
    i = 0

    while i < len(lines):
        line = lines[i]
        new_lines.append(line)

        # 检测 createObjectURL 调用
        match = re.search(r'(const|let|var)\s+(\w+)\s*=\s*(?:window\.)?URL\.createObjectURL\(', line)
        if match:
            var_name = match.group(2)
            # 找到使用该 URL 的 download/a.click/赋值语句之后，添加 revoke
            # 策略：在当前语句块结束后（下一个空行或新语句块）添加 revoke
            # 简单策略：在下一个空行后添加
            j = i + 1
            while j < len(lines):
                new_lines.append(lines[j])
                # 找到 download.click() 或类似操作后的空行
                if lines[j].strip() == '' or (j > i + 3 and 'click()' in lines[j]):
                    # 添加 revoke
                    indent = re.match(r'^(\s*)', line).group(1)
                    revoke_line = f"{indent}try {{ URL.revokeObjectURL({var_name}); }} catch(e) {{}}"
                    new_lines.append(revoke_line)
                    changes += 1
                    i = j + 1
                    break
                j += 1
            else:
                i = j
            continue

        i += 1

    if changes > 0 and not test_mode:
        _write_file(filepath, '\n'.join(new_lines))
    return changes


# --- 下载 ---

def _download_chartjs(goal: Goal, test_mode: bool) -> int:
    """下载 Chart.js 到本地"""
    target = PROJECT_ROOT / "static/lib/chart.min.js"
    if target.exists() and target.stat().st_size > 100000:
        return 0  # 已存在

    if test_mode:
        return 1  # 只检查是否需要下载

    target.parent.mkdir(parents=True, exist_ok=True)
    url = "https://cdn.jsdelivr.net/npm/chart.js"
    try:
        urllib.request.urlretrieve(url, str(target))
    except Exception:
        # 尝试国内镜像
        url_cn = "https://cdn.bootcdn.net/ajax/libs/Chart.js/4.4.7/chart.umd.min.js"
        urllib.request.urlretrieve(url_cn, str(target))

    # 更新 index.html 引用
    html_path = PROJECT_ROOT / "static/index.html"
    content = _read_file(html_path)
    content = content.replace(
        '<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>',
        '<script src="/static/lib/chart.min.js"></script>'
    )
    _write_file(html_path, content)
    return 1


# --- HTML ---

def _update_viewport_meta(goal: Goal, test_mode: bool) -> int:
    """更新 viewport meta 标签"""
    changes = 0
    for f in goal.files:
        filepath = PROJECT_ROOT / f
        if not filepath.exists():
            continue
        content = _read_file(filepath)
        original = content

        # 替换或添加 viewport meta
        old_viewport = '<meta name="viewport" content="width=device-width, initial-scale=1.0"'
        new_viewport = '<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover"'

        if old_viewport in content and 'viewport-fit=cover' not in content:
            content = content.replace(old_viewport, new_viewport)
            changes += 1

        if content != original and not test_mode:
            _write_file(filepath, content)

    return changes


def _add_mobile_tabbar(goal: Goal, test_mode: bool) -> int:
    """在 index.html 中添加底部 Tab Bar HTML"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)

    if 'mobile-tabbar' in content:
        return 0

    tabbar_html = """
    <!-- 移动端底部 Tab Bar -->
    <nav class="mobile-tabbar" id="mobileTabbar">
        <button class="tabbar-item active" onclick="showSection('dashboard')" data-section="dashboard">
            <i class="bi bi-house-door"></i><span>首页</span>
        </button>
        <button class="tabbar-item" onclick="showSection('accounts')" data-section="accounts">
            <i class="bi bi-person"></i><span>账号</span>
        </button>
        <button class="tabbar-item" onclick="showSection('online-im')" data-section="online-im">
            <i class="bi bi-chat-dots"></i><span>消息</span>
        </button>
        <button class="tabbar-item" onclick="showSection('orders')" data-section="orders">
            <i class="bi bi-receipt"></i><span>订单</span>
        </button>
        <button class="tabbar-item" onclick="showSection('system-settings')" data-section="system-settings">
            <i class="bi bi-gear"></i><span>我的</span>
        </button>
    </nav>
"""

    # 在 </body> 前插入
    content = content.replace('</body>', tabbar_html + '</body>')

    if not test_mode:
        _write_file(filepath, content)
    return 1


def _add_swipe_gesture(goal: Goal, test_mode: bool) -> int:
    """在 app.js 末尾添加 swipe 手势支持"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)

    if 'swipe-gesture' in content or 'touchstart' in content:
        return 0

    swipe_js = """
// === Swipe Gesture Support (WeChat Mobile) ===
(function() {
    let touchStartX = 0, touchStartY = 0, touchEndX = 0;
    const SIDEBAR_WIDTH = 260;

    document.addEventListener('touchstart', function(e) {
        touchStartX = e.changedTouches[0].screenX;
        touchStartY = e.changedTouches[0].screenY;
    }, { passive: true });

    document.addEventListener('touchend', function(e) {
        touchEndX = e.changedTouches[0].screenX;
        const diffX = touchEndX - touchStartX;
        const diffY = Math.abs(e.changedTouches[0].screenY - touchStartY);
        if (diffY > 80) return; // 垂直滑动忽略

        const sidebar = document.querySelector('.sidebar');
        if (!sidebar) return;

        if (diffX > 60 && touchStartX < 40) {
            // 右滑打开侧边栏
            sidebar.classList.add('show');
        } else if (diffX < -60 && sidebar.classList.contains('show')) {
            // 左滑关闭侧边栏
            sidebar.classList.remove('show');
        }
    }, { passive: true });
})();
"""

    content = content.rstrip() + '\n' + swipe_js

    if not test_mode:
        _write_file(filepath, content)
    return 1


# --- CSS ---

def _mobile_sidebar_backdrop(goal: Goal, test_mode: bool) -> int:
    """添加侧边栏遮罩层样式"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)

    if 'sidebar-backdrop' in content:
        return 0

    css = """
/* 移动端侧边栏遮罩层 */
.sidebar-backdrop {
    display: none;
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.5);
    z-index: 1040;
    transition: opacity 0.3s;
}
.sidebar-backdrop.show {
    display: block;
}
"""

    # 在 768px media query 之前插入
    content = content.replace(
        '@media (max-width: 768px)',
        css + '\n@media (max-width: 768px)'
    )

    if not test_mode:
        _write_file(filepath, content)
    return 1


def _mobile_padding_fix(goal: Goal, test_mode: bool) -> int:
    """移动端 padding 适配"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)

    if 'mobile-padding-fix' in content:
        return 0

    css = """
/* mobile-padding-fix */
@media (max-width: 768px) {
    .content-body { padding: 0.75rem !important; }
    .content-header { padding: 0.75rem !important; }
    .card { margin-bottom: 0.75rem; }
    .card-body { padding: 0.75rem; }
    .modal-dialog { margin: 0.5rem; max-width: calc(100vw - 1rem); }
    .modal-body { padding: 1rem; max-height: 80vh; overflow-y: auto; }
    iframe { max-height: 80vh !important; height: auto !important; }
    .table { font-size: 0.85rem; }
    .btn-sm { padding: 0.35rem 0.6rem; font-size: 0.8rem; }
}
"""

    content = content.rstrip() + '\n' + css

    if not test_mode:
        _write_file(filepath, content)
    return 1


def _mobile_chat_layout(goal: Goal, test_mode: bool) -> int:
    """聊天三栏布局移动端适配"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)

    if 'mobile-chat-layout' in content:
        return 0

    css = """
/* mobile-chat-layout */
@media (max-width: 768px) {
    .chat-container { flex-direction: column !important; height: auto !important; }
    .chat-account-list { width: 100% !important; max-height: 200px; border-right: none !important; border-bottom: 1px solid var(--bs-border-color, #dee2e6); }
    .chat-session-list { width: 100% !important; max-height: 250px; border-right: none !important; border-bottom: 1px solid var(--bs-border-color, #dee2e6); }
    .chat-reply-panel { width: 100% !important; min-height: 300px; }
}
"""

    content = content.rstrip() + '\n' + css

    if not test_mode:
        _write_file(filepath, content)
    return 1


def _mobile_modal_fix(goal: Goal, test_mode: bool) -> int:
    """Modal 移动端适配"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)

    if 'mobile-modal-fix' in content:
        return 0

    css = """
/* mobile-modal-fix */
@media (max-width: 768px) {
    .modal-xl, .modal-lg { max-width: calc(100vw - 1rem) !important; }
    .modal-content { border-radius: 0.75rem; }
    .modal-header { padding: 0.75rem 1rem; }
    .modal-footer { padding: 0.5rem 1rem; }
    .modal iframe { width: 100% !important; max-height: 70vh !important; }
}
"""

    content = content.rstrip() + '\n' + css

    if not test_mode:
        _write_file(filepath, content)
    return 1


def _mobile_touch_targets(goal: Goal, test_mode: bool) -> int:
    """触摸目标放大"""
    changes = 0
    for f in goal.files:
        filepath = PROJECT_ROOT / f
        if not filepath.exists():
            continue
        content = _read_file(filepath)

        if 'mobile-touch-targets' in content:
            continue

        css = """
/* mobile-touch-targets */
@media (max-width: 768px) {
    .btn { min-height: 44px; }
    .nav-link { min-height: 44px; display: flex; align-items: center; }
    .form-control { min-height: 44px; font-size: 16px; } /* 16px 防止 iOS 缩放 */
    .form-select { min-height: 44px; font-size: 16px; }
    .input-group-text { min-height: 44px; }
    .dropdown-item { min-height: 44px; display: flex; align-items: center; }
    .badge { padding: 0.4em 0.6em; }
}
"""
        content = content.rstrip() + '\n' + css

        if not test_mode:
            _write_file(filepath, content)
        changes += 1

    return changes


def _mobile_bottom_tabbar_css(goal: Goal, test_mode: bool) -> int:
    """底部 Tab Bar 样式"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)

    if 'mobile-tabbar' in content and '.mobile-tabbar' in content:
        return 0

    css = """
/* 移动端底部 Tab Bar */
.mobile-tabbar {
    display: none;
    position: fixed;
    bottom: 0; left: 0; right: 0;
    height: 56px;
    background: #fff;
    border-top: 1px solid #e0e0e0;
    z-index: 1050;
    justify-content: space-around;
    align-items: center;
    padding-bottom: env(safe-area-inset-bottom, 0);
    box-shadow: 0 -2px 8px rgba(0,0,0,0.06);
}
[data-theme="dark"] .mobile-tabbar {
    background: #1a1a2e;
    border-top-color: #333;
}
.tabbar-item {
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; flex: 1; height: 100%;
    border: none; background: none; color: #666;
    font-size: 0.65rem; padding: 4px 0; cursor: pointer;
    transition: color 0.2s;
}
.tabbar-item i { font-size: 1.25rem; margin-bottom: 2px; }
.tabbar-item.active { color: var(--bs-primary, #6366f1); }
[data-theme="dark"] .tabbar-item { color: #999; }
[data-theme="dark"] .tabbar-item.active { color: #818cf8; }

@media (max-width: 768px) {
    .mobile-tabbar { display: flex; }
    .content-wrapper { padding-bottom: 64px !important; } /* 为 tabbar 留空间 */
    .sidebar { transform: translateX(-100%); transition: transform 0.3s ease; z-index: 1060; }
    .sidebar.show { transform: translateX(0); }
    .mobile-menu-btn { display: block !important; }
}
"""

    content = content.rstrip() + '\n' + css

    if not test_mode:
        _write_file(filepath, content)
    return 1


def _mobile_section_animation(goal: Goal, test_mode: bool) -> int:
    """页面切换动画"""
    filepath = PROJECT_ROOT / goal.files[0]
    content = _read_file(filepath)

    if 'section-fade-in' in content:
        return 0

    css = """
/* 页面切换动画 */
@media (max-width: 768px) {
    .content-section.active {
        animation: sectionFadeIn 0.25s ease-out;
    }
    @keyframes sectionFadeIn {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
    }
}
"""

    content = content.rstrip() + '\n' + css

    if not test_mode:
        _write_file(filepath, content)
    return 1


# ============================================================
# Handler 注册表
# ============================================================

GOAL_HANDLERS: dict[str, Callable] = {
    "fix_optional_chaining": _fix_optional_chaining,
    "fix_window_open": _fix_window_open,
    "add_abortcontroller_polyfill": _add_abortcontroller_polyfill,
    "fix_createobjecturl_leak": _fix_createobjecturl_leak,
    "download_chartjs": _download_chartjs,
    "update_viewport_meta": _update_viewport_meta,
    "add_mobile_tabbar": _add_mobile_tabbar,
    "add_swipe_gesture": _add_swipe_gesture,
    "mobile_sidebar_backdrop": _mobile_sidebar_backdrop,
    "mobile_padding_fix": _mobile_padding_fix,
    "mobile_chat_layout": _mobile_chat_layout,
    "mobile_modal_fix": _mobile_modal_fix,
    "mobile_touch_targets": _mobile_touch_targets,
    "mobile_bottom_tabbar_css": _mobile_bottom_tabbar_css,
    "mobile_section_animation": _mobile_section_animation,
}


# ============================================================
# Harness：执行引擎
# ============================================================

def _init_log():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    return open(LOG_FILE, 'a', encoding='utf-8')


def _log(logf, msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    logf.write(line + '\n')
    logf.flush()


def _save_status(goals_status: dict):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(goals_status, f, ensure_ascii=False, indent=2)


def _load_status() -> dict:
    if STATUS_FILE.exists():
        return json.loads(STATUS_FILE.read_text(encoding='utf-8'))
    return {}


def _worker_run(goal_dict: dict, test_mode: bool) -> dict:
    """子进程入口"""
    goal = Goal(**goal_dict)
    return run_goal(goal, test_mode)


def preflight(goals: list[Goal]) -> bool:
    """预检：验证所有前置条件"""
    logf = _init_log()
    _log(logf, "=" * 60)
    _log(logf, "预检开始")
    _log(logf, "=" * 60)

    ok = True

    # 1. 检查项目目录
    if not PROJECT_ROOT.exists():
        _log(logf, f"[FAIL] 项目目录不存在: {PROJECT_ROOT}")
        ok = False
    else:
        _log(logf, f"[OK] 项目目录: {PROJECT_ROOT}")

    # 2. 检查所有涉及的文件
    all_files = set()
    for g in goals:
        for f in g.files:
            all_files.add(f)

    for f in sorted(all_files):
        fp = PROJECT_ROOT / f
        if f.endswith('.min.js'):
            # 目标文件，可能还不存在
            _log(logf, f"[OK] 目标文件（将创建）: {f}")
        elif fp.exists():
            _log(logf, f"[OK] 文件存在: {f} ({fp.stat().st_size} bytes)")
        else:
            _log(logf, f"[FAIL] 文件不存在: {f}")
            ok = False

    # 3. 检查磁盘空间
    stat = os.statvfs(str(PROJECT_ROOT))
    free_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
    if free_mb < 100:
        _log(logf, f"[FAIL] 磁盘空间不足: {free_mb:.0f}MB")
        ok = False
    else:
        _log(logf, f"[OK] 磁盘可用空间: {free_mb:.0f}MB")

    # 4. 检查网络（Chart.js 下载需要）
    try:
        urllib.request.urlopen("https://cdn.jsdelivr.net", timeout=5)
        _log(logf, "[OK] 网络连通（cdn.jsdelivr.net）")
    except Exception:
        _log(logf, "[WARN] cdn.jsdelivr.net 不可达，将使用国内镜像")

    # 5. 检查备份目录
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    _log(logf, f"[OK] 备份目录: {BACKUP_DIR}")

    # 6. 检查 Python 版本
    _log(logf, f"[OK] Python {sys.version.split()[0]}")

    _log(logf, "=" * 60)
    _log(logf, f"预检结果: {'通过' if ok else '失败'}")
    _log(logf, "=" * 60)

    logf.close()
    return ok


def launch(goals: list[Goal], test_mode: bool = False):
    """全量执行或试跑"""
    logf = _init_log()
    mode = "试跑" if test_mode else "全量执行"
    _log(logf, "=" * 60)
    _log(logf, f"{mode}开始 ({len(goals)} 个任务)")
    _log(logf, "=" * 60)

    # 初始化状态
    goals_status = {
        "started_at": datetime.now().isoformat(),
        "mode": mode,
        "goals": {}
    }
    for g in goals:
        goals_status["goals"][g.id] = {"status": "pending", "changes": 0, "error": None}
    _save_status(goals_status)

    # 试跑只执行第一个任务
    if test_mode:
        goals = [goals[0]]
        _log(logf, f"试跑模式：只执行第一个任务 [{goals[0].id}]")

    # 多进程并行执行
    goal_dicts = [asdict(g) for g in goals]
    total_changes = 0
    success = 0
    failed = 0

    with Pool(processes=min(WORKERS, len(goals))) as pool:
        results = pool.starmap(_worker_run, [(gd, test_mode) for gd in goal_dicts])

    for r in results:
        gid = r["id"]
        status = r["status"]
        changes = r["changes"]
        error = r["error"]

        goals_status["goals"][gid] = {
            "status": status,
            "changes": changes,
            "error": error,
            "completed_at": datetime.now().isoformat()
        }

        if status == "done":
            _log(logf, f"[DONE] {r['name']} - {changes} 处修改")
            total_changes += changes
            success += 1
        elif status == "failed":
            _log(logf, f"[FAIL] {r['name']} - {error}")
            failed += 1
        else:
            _log(logf, f"[SKIP] {r['name']} - {error}")
            failed += 1

    goals_status["finished_at"] = datetime.now().isoformat()
    goals_status["summary"] = {
        "total": len(goals),
        "success": success,
        "failed": failed,
        "total_changes": total_changes
    }
    _save_status(goals_status)

    _log(logf, "=" * 60)
    _log(logf, f"{mode}完成: 成功 {success}, 失败 {failed}, 总修改 {total_changes} 处")
    _log(logf, f"日志: {LOG_FILE}")
    _log(logf, f"状态: {STATUS_FILE}")
    _log(logf, "=" * 60)

    # 试跑后清理测试产物
    if test_mode:
        _log(logf, "试跑完成，正在回滚测试修改...")
        rollback(goals_status, logf)
        _log(logf, "试跑产物已清理")

    logf.close()


def rollback(goals_status: dict, logf=None):
    """从备份回滚所有修改"""
    if logf is None:
        logf = _init_log()

    _log(logf, "开始回滚...")
    restored = 0

    for backup_file in BACKUP_DIR.rglob("*"):
        if backup_file.is_file():
            rel = backup_file.relative_to(BACKUP_DIR)
            target = PROJECT_ROOT / rel
            shutil.copy2(backup_file, target)
            _log(logf, f"  恢复: {rel}")
            restored += 1

    _log(logf, f"回滚完成: 恢复 {restored} 个文件")


def show_status():
    """显示当前状态"""
    status = _load_status()
    if not status:
        print("没有找到执行记录。")
        return

    print(f"模式: {status.get('mode', '未知')}")
    print(f"开始: {status.get('started_at', '未知')}")
    print(f"结束: {status.get('finished_at', '进行中...')}")

    summary = status.get('summary', {})
    if summary:
        print(f"\n结果: 成功 {summary.get('success', 0)}, "
              f"失败 {summary.get('failed', 0)}, "
              f"总修改 {summary.get('total_changes', 0)} 处")

    print("\n各任务状态:")
    for gid, gs in status.get('goals', {}).items():
        icon = {"done": "OK", "failed": "XX", "pending": ".."}.get(gs['status'], '??')
        print(f"  [{icon}] {gid}: {gs['status']} ({gs.get('changes', 0)} changes)")
        if gs.get('error'):
            print(f"       错误: {gs['error']}")


# ============================================================
# CLI
# ============================================================

def main():
    goals = build_goals()

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "--preflight":
        ok = preflight(goals)
        sys.exit(0 if ok else 1)

    elif cmd == "--test":
        if not preflight(goals):
            print("预检失败，中止。")
            sys.exit(1)
        launch(goals, test_mode=True)

    elif cmd == "--launch":
        if not preflight(goals):
            print("预检失败，中止。")
            sys.exit(1)
        launch(goals, test_mode=False)

    elif cmd == "--status":
        show_status()

    elif cmd == "--rollback":
        logf = _init_log()
        status = _load_status()
        rollback(status, logf)
        logf.close()

    else:
        print(f"未知命令: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()

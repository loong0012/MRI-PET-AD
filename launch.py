"""
ADScreen · 阿尔兹海默病多模态影像智能筛查平台 - 一键启动入口

三种启动方式：
1. IDE 内：按 F5（VS Code/Trae 已配置 .vscode/launch.json）
2. 命令行：python launch.py
3. 双击 Run ADScreen.lnk（如 .lnk 关联正常）

启动流程：
1. 环境检测（.venv / 后端入口 / 前端入口）
2. 自动复制 .env（缺失则从 .env.example）
3. 自动 npm install（前端 node_modules 缺失时）
4. 启动后端 uvicorn :8001
5. 启动前端 vite :5173
6. 等待服务就绪后自动打开浏览器
7. 主进程等待 Ctrl+C，关闭时优雅停止子进程

IDE 内联模式（默认在 VS Code/Trae 终端中）：
- 后端/前端子进程的输出实时打印到当前终端（带前缀区分）
- Ctrl+C 一键停止所有服务
"""
import os
import sys
import time
import subprocess
import webbrowser
import threading
from pathlib import Path

# 强制 Windows 控制台 UTF-8 输出，避免中文乱码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 项目根目录（脚本所在目录）
ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "ad-screen-backend"
FRONTEND_DIR = ROOT / "ad-screen-frontend"
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"

# 颜色（ANSI，Windows 10+ 支持）
def c(text: str, color: str = "") -> str:
    if not color:
        return text
    colors = {
        "green": "\033[92m", "red": "\033[91m", "yellow": "\033[93m",
        "cyan": "\033[96m", "bold": "\033[1m", "reset": "\033[0m",
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"

# Windows 10+ 启用 ANSI 颜色支持
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass


def step(msg: str) -> None:
    print(f"{c('[*]', 'cyan')} {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"{c('[√]', 'green')} {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"{c('[!]', 'yellow')} {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"{c('[X]', 'red')} {msg}", flush=True)


def banner() -> None:
    print()
    print(c("=" * 60, "bold"))
    print(c("  ADScreen · 阿尔兹海默病多模态影像智能筛查平台", "bold"))
    print(c("  一键启动脚本 (Python)", "bold"))
    print(c("=" * 60, "bold"))
    print()


def is_ide_context() -> bool:
    """检测是否在 IDE 终端中运行（VS Code / Trae 等）"""
    # VS Code/Trae 设置 VSCODE_PID 或 TERM_PROGRAM=vscode
    if os.environ.get("VSCODE_PID"):
        return True
    if os.environ.get("TERM_PROGRAM") in ("vscode", "Trae"):
        return True
    if os.environ.get("TRAECODE_PID"):
        return True
    return False


def check_env() -> bool:
    """环境检测：返回是否通过"""
    step("环境检测中...")
    if not VENV_PY.exists():
        fail(f"未找到 {VENV_PY}")
        print("    请先在项目根目录创建虚拟环境：")
        print(f"    cd /d \"{ROOT}\"")
        print("    python -m venv .venv")
        print("    .venv\\Scripts\\activate")
        print("    pip install -r ad-screen-backend\\requirements.txt")
        return False
    if not (BACKEND_DIR / "main.py").exists():
        fail(f"未找到后端入口 {BACKEND_DIR / 'main.py'}")
        return False
    if not (FRONTEND_DIR / "package.json").exists():
        fail(f"未找到前端入口 {FRONTEND_DIR / 'package.json'}")
        return False
    ok("环境检测通过")
    return True


def prepare_backend_env() -> None:
    """后端 .env 缺失则从 .env.example 复制"""
    env_file = BACKEND_DIR / ".env"
    example = ROOT / ".env.example"
    if env_file.exists():
        ok("后端 .env 已存在")
        return
    if example.exists():
        import shutil
        shutil.copyfile(example, env_file)
        ok("后端 .env 从 .env.example 复制完成（使用开发默认配置）")
    else:
        warn("未找到 .env.example，后端将使用代码内置默认配置")


def prepare_frontend_deps() -> bool:
    """前端 node_modules 缺失则自动 npm install"""
    node_modules = FRONTEND_DIR / "node_modules"
    if node_modules.exists():
        ok("前端 node_modules 已存在")
        return True
    warn("首次运行：检测到前端 node_modules 缺失，开始安装依赖...")
    try:
        subprocess.check_call(["npm", "install"], cwd=str(FRONTEND_DIR), shell=True)
        ok("前端依赖安装完成")
        return True
    except subprocess.CalledProcessError as e:
        fail(f"前端依赖安装失败：{e}")
        print(f"    请手动执行：cd ad-screen-frontend && npm install")
        return False
    except FileNotFoundError:
        fail("未找到 npm 命令，请先安装 Node.js (https://nodejs.org)")
        return False


def _stream_output(proc: subprocess.Popen, prefix: str, color: str) -> None:
    """异步读取子进程 stdout 并加前缀打印到主终端"""
    prefix_str = c(f"[{prefix}]", color)
    try:
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            line = line.rstrip("\r\n")
            if line:
                print(f"{prefix_str} {line}", flush=True)
    except Exception:
        pass


def start_backend_inline() -> subprocess.Popen:
    """IDE 内联模式：后端子进程输出实时打印到当前终端"""
    step("正在启动后端服务 (http://localhost:8001)...")
    cmd = [str(VENV_PY), "-m", "uvicorn", "main:app", "--reload", "--port", "8001"]
    proc = subprocess.Popen(
        cmd,
        cwd=str(BACKEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    # 后台线程持续读取输出
    threading.Thread(target=_stream_output, args=(proc, "BACKEND", "green"), daemon=True).start()
    return proc


def start_frontend_inline() -> subprocess.Popen:
    """IDE 内联模式：前端子进程输出实时打印到当前终端"""
    step("正在启动前端服务 (http://localhost:5173)...")
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    proc = subprocess.Popen(
        [npm_cmd, "run", "dev"],
        cwd=str(FRONTEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        shell=False,
    )
    threading.Thread(target=_stream_output, args=(proc, "FRONTEND", "cyan"), daemon=True).start()
    return proc


def start_backend_windowed() -> subprocess.Popen:
    """独立窗口模式：后端在新 cmd 窗口启动"""
    step("正在启动后端服务 (http://localhost:8001)...")
    title = "ADScreen Backend :8001"
    cmd = [str(VENV_PY), "-m", "uvicorn", "main:app", "--reload", "--port", "8001"]
    full_cmd = f'title {title} && "{" ".join(cmd)}"'
    proc = subprocess.Popen(
        ["cmd", "/c", "start", title, "cmd", "/k", full_cmd],
        cwd=str(BACKEND_DIR),
        shell=False,
    )
    return proc


def start_frontend_windowed() -> subprocess.Popen:
    """独立窗口模式：前端在新 cmd 窗口启动"""
    step("正在启动前端服务 (http://localhost:5173)...")
    title = "ADScreen Frontend :5173"
    full_cmd = f'title {title} && npm run dev'
    proc = subprocess.Popen(
        ["cmd", "/c", "start", title, "cmd", "/k", full_cmd],
        cwd=str(FRONTEND_DIR),
        shell=False,
    )
    return proc


def wait_for_url(url: str, timeout: int = 30) -> bool:
    """轮询等待 URL 可达"""
    import urllib.request
    import urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(1)
    return False


def show_access_info() -> None:
    print()
    print(c("=" * 60, "bold"))
    print(c("  启动中，请稍候...", "bold"))
    print(c("  后端 API 文档：    http://localhost:8001/docs", "cyan"))
    print(c("  前端访问地址：     http://localhost:5173", "cyan"))
    print(c("=" * 60, "bold"))
    print()
    print("  演示账号：")
    print("    超级管理员   admin / admin123")
    print("    放射科医师   rad01 / 123456")
    print("    神经内科医师 neu01 / 123456")
    print("    科研管理员   sci01 / 123456")
    print()


def stop_proc(proc: subprocess.Popen, name: str) -> None:
    """优雅终止子进程"""
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        step(f"{name} 已停止")
    except Exception as e:
        warn(f"停止 {name} 时出错：{e}")


def main() -> int:
    banner()
    ide_mode = is_ide_context()
    if ide_mode:
        ok("检测到 IDE 终端环境，使用内联模式（子进程输出集中到当前终端）")
    else:
        ok("检测到非 IDE 环境，将弹出独立后端/前端窗口")

    if not check_env():
        input("按回车键退出...")
        return 1

    prepare_backend_env()
    if not prepare_frontend_deps():
        input("按回车键退出...")
        return 1

    if ide_mode:
        backend_proc = start_backend_inline()
        time.sleep(2)
        frontend_proc = start_frontend_inline()
    else:
        backend_proc = start_backend_windowed()
        time.sleep(2)
        frontend_proc = start_frontend_windowed()

    show_access_info()

    # 等待服务就绪后打开浏览器
    step("等待前端服务就绪...")
    if wait_for_url("http://localhost:5173", timeout=30):
        ok("前端已就绪，正在打开浏览器...")
        try:
            webbrowser.open("http://localhost:5173")
        except Exception:
            warn("浏览器自动打开失败，请手动访问 http://localhost:5173")
    else:
        warn("前端未在 30 秒内就绪，请手动访问 http://localhost:5173")

    print()
    ok("启动流程完成")
    if ide_mode:
        print("    主进程保持运行，按 Ctrl+C 一键停止所有服务。")
    else:
        print("    主进程保持运行，按 Ctrl+C 退出主窗口。")
        print("    后端/前端弹窗独立运行，关闭它们即可停止服务。")
    print()

    # 主进程保持运行，直到用户 Ctrl+C
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print()
        step("收到 Ctrl+C，正在停止所有服务...")
        if ide_mode:
            stop_proc(frontend_proc, "前端")
            stop_proc(backend_proc, "后端")
        else:
            step("主窗口退出。后端/前端弹窗仍独立运行，请手动关闭它们。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        step("收到 Ctrl+C，主窗口退出。")
        sys.exit(0)
    except Exception as e:
        import traceback
        print()
        fail("启动器异常退出，详情如下：")
        traceback.print_exc()
        print()
        print("    常见原因：")
        print("    1) .venv 损坏 → 重建：python -m venv .venv --clear && pip install -r ad-screen-backend\\requirements.txt")
        print("    2) 后端端口 8001 被占用 → 任务管理器结束残留 python.exe")
        print("    3) 前端端口 5173 被占用 → 结束残留 node.exe")
        print()
        input("按回车键关闭本窗口...")
        sys.exit(1)

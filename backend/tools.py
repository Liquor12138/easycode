"""
工具定义与执行模块
定义 Agent 可用的工具（读写文件、查看目录、执行命令），
并实现安全性检查：路径沙箱、危险命令拦截、超时控制。
"""

import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional

from config import Config

# ============================================================
# 工具描述 (OpenAI function calling 格式，DeepSeek 兼容)
# ============================================================

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定路径文件的内容。路径可以是绝对路径或相对于工作目录的相对路径。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要读取的文件路径（相对路径以工作目录为基准）",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认 utf-8",
                        "default": "utf-8",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入指定路径的文件。若文件不存在则自动创建，已存在则覆盖。自动创建所需的父目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要写入的文件路径（相对路径以工作目录为基准）",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的文件内容",
                    },
                    "encoding": {
                        "type": "string",
                        "description": "文件编码，默认 utf-8",
                        "default": "utf-8",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "列出指定目录的内容，返回文件名/目录名列表及其类型标识。默认列出工作目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要列出的目录路径（相对路径以工作目录为基准），默认 '.'",
                        "default": ".",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "是否递归列出子目录内容，默认 false",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "在工作目录中执行终端命令。支持系统命令和已安装的可执行程序。命令超时时间由配置决定。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的终端命令",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "命令执行目录，默认为当前工作目录",
                        "default": ".",
                    },
                },
                "required": ["command"],
            },
        },
    },
]

# ============================================================
# 安全检查
# ============================================================

# Windows 危险命令关键字（小写匹配）
_DANGEROUS_PATTERNS = [
    "format ",        # 格式化磁盘
    "del /",          # 递归删除
    "remove-item",    # PowerShell 删除
    "rm -rf",         # Unix 递归删除
    "rm -r ",         # Unix 递归删除
    "rmdir /s",       # Windows 递归删除目录
    "diskpart",       # 磁盘分区操作
    "reg delete",     # 注册表删除
    "reg add",        # 注册表修改
    "shutdown",       # 关机
    "taskkill /f",    # 强制杀进程
    "kill -9",        # Unix 强制杀进程
    "net stop",       # 停止服务
    "bcdedit",        # 启动配置编辑
    "takeown",        # 获取文件所有权
    "icacls",         # 修改权限
]


def _resolve_path(path: str, working_dir: str) -> Path:
    """将路径解析为绝对路径，确保在沙箱内。"""
    p = Path(path)
    if not p.is_absolute():
        p = Path(working_dir) / p
    resolved = p.resolve()
    wd_resolved = Path(working_dir).resolve()

    # 安全检查：路径必须在工作目录内
    try:
        resolved.relative_to(wd_resolved)
    except ValueError:
        raise PermissionError(
            f"安全限制：路径 '{path}' 解析为 '{resolved}'，"
            f"不在允许的工作目录 '{wd_resolved}' 内。"
        )
    return resolved


def _check_command_safety(command: str) -> None:
    """检查命令是否包含危险操作。"""
    lower_cmd = command.lower().strip()

    for pattern in _DANGEROUS_PATTERNS:
        if pattern in lower_cmd:
            raise ValueError(
                f"安全限制：命令包含危险操作 '{pattern.strip()}'，已被拦截。\n"
                f"被拦截的命令: {command}"
            )


# ============================================================
# 工具执行
# ============================================================

def execute_tool(tool_name: str, arguments: dict, working_dir: str) -> str:
    """
    根据工具名和参数执行对应操作，返回字符串结果。
    所有文件操作都经过路径沙箱检查。
    """
    try:
        if tool_name == "read_file":
            return _read_file(arguments, working_dir)
        elif tool_name == "write_file":
            return _write_file(arguments, working_dir)
        elif tool_name == "list_directory":
            return _list_directory(arguments, working_dir)
        elif tool_name == "execute_command":
            return _execute_command(arguments, working_dir)
        else:
            return f"错误：未知工具 '{tool_name}'"
    except PermissionError as e:
        return f"安全限制：{e}"
    except ValueError as e:
        return f"参数错误：{e}"
    except TimeoutError as e:
        return f"执行超时：{e}"
    except Exception as e:
        return f"执行出错：{type(e).__name__}: {e}"


def _read_file(args: dict, working_dir: str) -> str:
    """读取文件内容。"""
    file_path = _resolve_path(args["path"], working_dir)

    if not file_path.exists():
        return f"错误：文件不存在 '{args['path']}'"
    if not file_path.is_file():
        return f"错误：'{args['path']}' 不是一个文件"

    # 检查文件大小
    size = file_path.stat().st_size
    if size > Config.MAX_FILE_SIZE:
        return f"错误：文件过大 ({size} bytes)，超过限制 ({Config.MAX_FILE_SIZE} bytes)"

    encoding = args.get("encoding", "utf-8")
    try:
        content = file_path.read_text(encoding=encoding)
    except UnicodeDecodeError:
        # 尝试二进制读取
        content = file_path.read_bytes().decode("latin-1")

    # 截断过长内容
    max_chars = 50000
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n... [内容已截断，共 {len(content)} 字符] ..."

    return content


def _write_file(args: dict, working_dir: str) -> str:
    """写入文件内容。"""
    file_path = _resolve_path(args["path"], working_dir)
    content = args["content"]
    encoding = args.get("encoding", "utf-8")

    # 自动创建父目录
    file_path.parent.mkdir(parents=True, exist_ok=True)

    file_path.write_text(content, encoding=encoding)
    return f"成功写入文件 '{file_path}' ({len(content)} 字符)"


def _list_directory(args: dict, working_dir: str) -> str:
    """列出目录内容。"""
    dir_path = _resolve_path(args.get("path", "."), working_dir)
    recursive = args.get("recursive", False)

    if not dir_path.exists():
        return f"错误：目录不存在 '{args.get('path', '.')}'"
    if not dir_path.is_dir():
        return f"错误：'{args.get('path', '.')}' 不是一个目录"

    lines = []
    base_indent = 0

    def _list_dir(d: Path, indent: int):
        try:
            entries = sorted(d.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except PermissionError:
            lines.append(f"{'  ' * indent}[权限不足，无法读取]")
            return

        for entry in entries:
            prefix = "[DIR]  " if entry.is_dir() else "[FILE] "
            lines.append(f"{'  ' * indent}{prefix}{entry.name}")
            if recursive and entry.is_dir() and indent < 3:  # 最多递归3层
                _list_dir(entry, indent + 1)

    lines.append(f"目录: {dir_path}")
    lines.append("-" * 40)
    _list_dir(dir_path, 0)

    result = "\n".join(lines)
    # 截断过长输出
    if len(result) > 20000:
        result = result[:20000] + "\n\n... [目录列表已截断] ..."
    return result


def _execute_command(args: dict, working_dir: str) -> str:
    """执行终端命令。"""
    command = args["command"]
    cwd = args.get("cwd", ".")

    # 安全检查
    _check_command_safety(command)

    # 解析工作目录
    exec_dir = _resolve_path(cwd, working_dir)
    if not exec_dir.is_dir():
        return f"错误：执行目录不存在 '{cwd}'"

    # 判断是否使用 shell
    use_shell = True
    executable = None
    if os.name == "nt":
        executable = "cmd.exe"

    try:
        result = subprocess.run(
            command,
            shell=use_shell,
            executable=executable,
            capture_output=True,
            text=True,
            cwd=str(exec_dir),
            timeout=Config.COMMAND_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )

        output_parts = []
        if result.stdout:
            output_parts.append(result.stdout.rstrip())
        if result.stderr:
            output_parts.append(f"[STDERR] {result.stderr.rstrip()}")
        if result.returncode != 0:
            output_parts.append(f"[退出码: {result.returncode}]")

        output = "\n".join(output_parts) if output_parts else "(命令执行成功，无输出)"

        # 截断过长输出
        if len(output) > 30000:
            output = output[:30000] + "\n\n... [输出已截断] ..."

        return output

    except subprocess.TimeoutExpired:
        raise TimeoutError(
            f"命令在 {Config.COMMAND_TIMEOUT} 秒内未完成，已终止。"
        )
    except Exception as e:
        return f"命令执行失败: {e}"

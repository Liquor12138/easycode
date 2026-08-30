"""
工具定义与执行模块
定义 Agent 可用的工具：
  - 文件操作：读写文件、查看目录
  - 终端执行：执行命令（含安全检查）
  - 计划管理：制定计划、标记步骤、完成任务
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
    # ---- 计划管理工具 ----
    {
        "type": "function",
        "function": {
            "name": "create_plan",
            "description": "在开始执行任务前，制定一个分步完成计划。每个步骤将按顺序执行和跟踪。应在执行任何实际操作之前调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "计划的简短标题",
                    },
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "按顺序排列的任务步骤描述列表",
                    },
                },
                "required": ["title", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_step",
            "description": "将一个计划步骤标记为已完成。每完成一个步骤后应立即调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "step_index": {
                        "type": "integer",
                        "description": "要标记为完成的步骤索引（从 0 开始）",
                    },
                    "result": {
                        "type": "string",
                        "description": "该步骤的完成结果简述",
                    },
                },
                "required": ["step_index", "result"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_task",
            "description": "所有计划步骤完成后调用，提交最终总结并结束任务。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "任务完成的最终总结，包括完成了什么、创建/修改了哪些文件、执行了哪些关键命令",
                    },
                },
                "required": ["summary"],
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

def execute_tool(
    tool_name: str,
    arguments: dict,
    working_dir: str,
    plan_state: Optional[dict] = None,
) -> str:
    """
    根据工具名和参数执行对应操作，返回字符串结果。
    所有文件操作都经过路径沙箱检查。
    plan_state 用于计划管理工具的状态存储（由 Agent 传入）。
    """
    try:
        # ---- 文件操作工具 ----
        if tool_name == "read_file":
            return _read_file(arguments, working_dir)
        elif tool_name == "write_file":
            return _write_file(arguments, working_dir)
        elif tool_name == "list_directory":
            return _list_directory(arguments, working_dir)
        elif tool_name == "execute_command":
            return _execute_command(arguments, working_dir)
        # ---- 计划管理工具 ----
        elif tool_name == "create_plan":
            return _create_plan(arguments, plan_state)
        elif tool_name == "update_step":
            return _update_step(arguments, plan_state)
        elif tool_name == "finish_task":
            return _finish_task(arguments, plan_state)
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


# ============================================================
# 计划管理工具
# ============================================================

def _create_plan(args: dict, plan_state: Optional[dict]) -> str:
    """创建任务计划。"""
    if plan_state is None:
        return "错误：计划状态未初始化"

    title = args["title"]
    steps = args["steps"]

    if not steps:
        return "错误：步骤列表不能为空"

    plan_state["title"] = title
    plan_state["steps"] = [
        {"index": i, "description": s, "status": "pending", "result": ""}
        for i, s in enumerate(steps)
    ]
    plan_state["finished"] = False
    plan_state["summary"] = ""

    step_list = "\n".join(f"  [{i}] {s}" for i, s in enumerate(steps))
    return f"计划已创建：「{title}」\n共 {len(steps)} 个步骤：\n{step_list}\n\n请按顺序逐步执行，每完成一步调用 update_step 标记。全部完成后调用 finish_task 提交总结。"


def _update_step(args: dict, plan_state: Optional[dict]) -> str:
    """标记步骤完成。"""
    if plan_state is None or "steps" not in plan_state:
        return "错误：尚未创建计划，请先调用 create_plan"

    step_index = args["step_index"]
    result = args.get("result", "")
    steps = plan_state["steps"]

    if step_index < 0 or step_index >= len(steps):
        return f"错误：步骤索引 {step_index} 超出范围（共 {len(steps)} 步，索引 0~{len(steps)-1}）"

    step = steps[step_index]
    step["status"] = "completed"
    step["result"] = result

    # 统计进度
    completed = sum(1 for s in steps if s["status"] == "completed")
    total = len(steps)
    remaining = [f"[{s['index']}] {s['description']}" for s in steps if s["status"] == "pending"]

    msg = f"步骤 [{step_index}] 已标记完成：「{result}」\n进度：{completed}/{total}"
    if remaining:
        msg += f"\n剩余步骤：{', '.join(remaining)}"
    else:
        msg += "\n所有步骤已完成！请调用 finish_task 提交最终总结。"
    return msg


def _finish_task(args: dict, plan_state: Optional[dict]) -> str:
    """完成任务，输出总结。"""
    if plan_state is None:
        return "错误：计划状态未初始化"

    summary = args["summary"]
    plan_state["finished"] = True
    plan_state["summary"] = summary

    # 附带计划执行概况
    if "steps" in plan_state:
        completed = sum(1 for s in plan_state["steps"] if s["status"] == "completed")
        total = len(plan_state["steps"])
        overview = "\n".join(
            f"  [{'v' if s['status'] == 'completed' else 'x'}] [{s['index']}] {s['description']}"
            for s in plan_state["steps"]
        )
        return f"任务完成。\n\n计划「{plan_state.get('title', '')}」执行情况：\n{overview}\n完成 {completed}/{total}\n\n总结：{summary}"
    return f"任务完成。总结：{summary}"

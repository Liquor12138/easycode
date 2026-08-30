"""
工具定义与执行模块
定义 Agent 可用的工具：
  - 文件操作：读写文件、查看目录
  - 终端执行：执行命令（含安全检查）
  - 计划管理：制定计划、标记步骤、完成任务
并实现安全性检查：路径沙箱、危险命令拦截、超时控制。
"""

import os
import re
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
    # ---- 代码编辑与搜索工具 ----
    {
        "type": "function",
        "function": {
            "name": "search_replace",
            "description": "在文件中精确查找一段文本并替换为新文本。适用于对文件进行局部修改，要求 old_text 在文件中唯一匹配。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要修改的文件路径",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "要被替换的原始文本（必须在文件中唯一匹配）",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "替换后的新文本",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_text",
            "description": "在一个文件或目录中搜索包含指定文本的行，返回匹配的行号和内容。支持正则表达式。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "要搜索的文本或正则表达式",
                    },
                    "path": {
                        "type": "string",
                        "description": "要搜索的文件或目录路径，默认工作目录",
                        "default": ".",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "文件名过滤 glob 模式（如 '*.py'），仅在搜索目录时生效",
                        "default": "",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "是否区分大小写，默认 true",
                        "default": True,
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_diagnostics",
            "description": "获取文件的编译器/linter 报错信息。根据文件扩展名自动选择检查工具（Python->py_compile，JS->node --check 等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要检查的文件路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_symbols",
            "description": "列出源代码文件中定义的符号：函数、类、变量、常量等。支持 Python、JavaScript/TypeScript、Java、C/C++ 等语言。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "要分析的源代码文件路径",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_workdir",
            "description": "获取当前工作目录的绝对路径。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
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
        # ---- 代码编辑与搜索工具 ----
        elif tool_name == "search_replace":
            return _search_replace(arguments, working_dir)
        elif tool_name == "search_text":
            return _search_text(arguments, working_dir)
        elif tool_name == "get_diagnostics":
            return _get_diagnostics(arguments, working_dir)
        elif tool_name == "list_symbols":
            return _list_symbols(arguments, working_dir)
        elif tool_name == "get_workdir":
            return _get_workdir(arguments, working_dir)
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


# ============================================================
# 代码编辑与搜索工具
# ============================================================

def _search_replace(args: dict, working_dir: str) -> str:
    """在文件中精确查找并替换文本。"""
    file_path = _resolve_path(args["path"], working_dir)

    if not file_path.exists():
        return f"错误：文件不存在 '{args['path']}'"
    if not file_path.is_file():
        return f"错误：'{args['path']}' 不是一个文件"

    old_text = args["old_text"]
    new_text = args["new_text"]

    content = file_path.read_text(encoding="utf-8")
    count = content.count(old_text)

    if count == 0:
        return "错误：未找到匹配的文本，请检查 old_text 是否与原文件完全一致（包括空格和缩进）"
    if count > 1:
        return f"错误：找到 {count} 处匹配，old_text 必须唯一。请提供更多上下文以确保唯一匹配。"

    new_content = content.replace(old_text, new_text, 1)
    file_path.write_text(new_content, encoding="utf-8")
    return f"成功替换 1 处匹配（{len(old_text)} 字符 -> {len(new_text)} 字符）"


def _search_text(args: dict, working_dir: str) -> str:
    """在文件或目录中搜索文本，返回匹配行号。"""
    pattern = args["pattern"]
    target = _resolve_path(args.get("path", "."), working_dir)
    file_pattern = args.get("file_pattern", "")
    case_sensitive = args.get("case_sensitive", True)

    # 编译正则
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"错误：无效的正则表达式 '{pattern}': {e}"

    results = []
    max_results = 100

    def _search_in_file(fpath: Path):
        if len(results) >= max_results:
            return
        try:
            lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            return
        for i, line in enumerate(lines, 1):
            if regex.search(line):
                rel = fpath.relative_to(Path(working_dir).resolve())
                results.append(f"  {rel}:{i}: {line.strip()}")
                if len(results) >= max_results:
                    results.append(f"  ... 已达到最大结果数 {max_results}，请缩小搜索范围")
                    return

    if target.is_file():
        _search_in_file(target)
    elif target.is_dir():
        # 遍历目录
        skip_dirs = {"__pycache__", "node_modules", ".git", ".venv", "venv", ".idea", "dist", "build"}
        for root, dirs, files in os.walk(str(target)):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fname in files:
                if file_pattern:
                    import fnmatch
                    if not fnmatch.fnmatch(fname, file_pattern):
                        continue
                _search_in_file(Path(root) / fname)
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break
    else:
        return f"错误：路径不存在 '{args.get('path', '.')}'"

    if not results:
        return f"未找到匹配 '{pattern}' 的内容"
    header = f"找到 {len(results)} 处匹配："
    return header + "\n" + "\n".join(results)


def _get_diagnostics(args: dict, working_dir: str) -> str:
    """获取文件的编译器/linter 报错。"""
    file_path = _resolve_path(args["path"], working_dir)

    if not file_path.exists():
        return f"错误：文件不存在 '{args['path']}'"
    if not file_path.is_file():
        return f"错误：'{args['path']}' 不是一个文件"

    ext = file_path.suffix.lower()

    # 根据文件类型选择检查命令
    check_commands = {
        ".py": ["python", "-m", "py_compile", str(file_path)],
        ".js": ["node", "--check", str(file_path)],
        ".ts": ["npx", "tsc", "--noEmit", str(file_path)],
        ".java": ["javac", "-d", str(file_path.parent), str(file_path)],
    }

    cmd = check_commands.get(ext)
    if cmd is None:
        return f"不支持的文件类型 '{ext}'。支持的类型：{', '.join(check_commands.keys())}"

    # 检查工具是否可用
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=Config.COMMAND_TIMEOUT,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return f"错误：未找到 '{cmd[0]}'，请确认已安装并加入 PATH"
    except subprocess.TimeoutExpired:
        return f"错误：检查超时 ({Config.COMMAND_TIMEOUT} 秒)"

    if result.returncode == 0:
        return f"文件 '{args['path']}' 无报错，检查通过。"

    output_parts = []
    if result.stdout:
        output_parts.append(result.stdout.strip())
    if result.stderr:
        output_parts.append(result.stderr.strip())
    output = "\n".join(output_parts)

    if len(output) > 10000:
        output = output[:10000] + "\n... [报错信息已截断] ..."
    return output


# 各语言的符号提取正则
_SYMBOL_PATTERNS = {
    ".py": [
        ("function", re.compile(r"^def\s+(\w+)\s*\(", re.MULTILINE)),
        ("class", re.compile(r"^class\s+(\w+)", re.MULTILINE)),
        ("variable", re.compile(r"^([A-Z_][A-Z_0-9]*)\s*=", re.MULTILINE)),
    ],
    ".js": [
        ("function", re.compile(r"(?:^|\s)(?:async\s+)?function\s+(\w+)", re.MULTILINE)),
        ("function", re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*=>", re.MULTILINE)),
        ("class", re.compile(r"^class\s+(\w+)", re.MULTILINE)),
        ("variable", re.compile(r"^(?:const|let|var)\s+([A-Z_][A-Z_0-9]*)\s*=", re.MULTILINE)),
    ],
    ".ts": [
        ("function", re.compile(r"(?:^|\s)(?:async\s+)?function\s+(\w+)", re.MULTILINE)),
        ("function", re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?(?:\([^)]*\)|\w+)\s*=>", re.MULTILINE)),
        ("class", re.compile(r"^(?:export\s+)?class\s+(\w+)", re.MULTILINE)),
        ("interface", re.compile(r"^(?:export\s+)?interface\s+(\w+)", re.MULTILINE)),
        ("type", re.compile(r"^(?:export\s+)?type\s+(\w+)", re.MULTILINE)),
        ("variable", re.compile(r"^(?:const|let|var)\s+([A-Z_][A-Z_0-9]*)\s*=", re.MULTILINE)),
    ],
    ".java": [
        ("class", re.compile(r"(?:public|private|protected)?\s*(?:static\s+)?(?:final\s+)?class\s+(\w+)", re.MULTILINE)),
        ("interface", re.compile(r"(?:public|private|protected)?\s*interface\s+(\w+)", re.MULTILINE)),
        ("function", re.compile(r"(?:public|private|protected)[\s\w]*\s+(\w+)\s*\(", re.MULTILINE)),
    ],
    ".c": [
        ("function", re.compile(r"^\w[\w\s\*]+\s+(\w+)\s*\([^)]*\)\s*\{", re.MULTILINE)),
        ("variable", re.compile(r"^#define\s+(\w+)", re.MULTILINE)),
        ("type", re.compile(r"^(?:typedef\s+)?(?:struct|enum|union)\s+(\w+)", re.MULTILINE)),
    ],
    ".cpp": [
        ("function", re.compile(r"^\w[\w\s\*:]+\s+(\w+)\s*\([^)]*\)\s*(?:const\s*)?\{", re.MULTILINE)),
        ("class", re.compile(r"^class\s+(\w+)", re.MULTILINE)),
        ("variable", re.compile(r"^#define\s+(\w+)", re.MULTILINE)),
        ("type", re.compile(r"^(?:typedef\s+)?(?:struct|enum|union|class)\s+(\w+)", re.MULTILINE)),
    ],
}
# 让 .jsx/.tsx 复用 .js/.ts 的规则
_SYMBOL_PATTERNS[".jsx"] = _SYMBOL_PATTERNS[".js"]
_SYMBOL_PATTERNS[".tsx"] = _SYMBOL_PATTERNS[".ts"]
_SYMBOL_PATTERNS[".cc"] = _SYMBOL_PATTERNS[".cpp"]
_SYMBOL_PATTERNS[".h"] = _SYMBOL_PATTERNS[".c"]
_SYMBOL_PATTERNS[".hpp"] = _SYMBOL_PATTERNS[".cpp"]


def _list_symbols(args: dict, working_dir: str) -> str:
    """列出文件中定义的符号。"""
    file_path = _resolve_path(args["path"], working_dir)

    if not file_path.exists():
        return f"错误：文件不存在 '{args['path']}'"
    if not file_path.is_file():
        return f"错误：'{args['path']}' 不是一个文件"

    ext = file_path.suffix.lower()
    patterns = _SYMBOL_PATTERNS.get(ext)
    if patterns is None:
        supported = ", ".join(sorted(_SYMBOL_PATTERNS.keys()))
        return f"不支持的文件类型 '{ext}'。支持的类型：{supported}"

    content = file_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    symbols = []  # (kind, name, line_number)
    for kind, regex in patterns:
        for m in regex.finditer(content):
            name = m.group(1)
            line_num = content[:m.start()].count("\n") + 1
            symbols.append((kind, name, line_num))

    if not symbols:
        return f"文件 '{args['path']}' 中未检测到符号定义"

    # 按行号排序
    symbols.sort(key=lambda x: x[2])

    # 格式化输出
    kind_labels = {
        "class": "CLASS",
        "function": "FUNC ",
        "variable": "VAR  ",
        "interface": "IFACE",
        "type": "TYPE ",
    }
    result_lines = [f"文件: {file_path.name}  ({ext}  {len(lines)} 行)"]
    result_lines.append("-" * 50)
    for kind, name, line_num in symbols:
        label = kind_labels.get(kind, kind.upper()[:5])
        result_lines.append(f"  L{line_num:<6} [{label}] {name}")

    return "\n".join(result_lines)


def _get_workdir(args: dict, working_dir: str) -> str:
    """返回当前工作目录。"""
    wd = Path(working_dir).resolve()
    entries = []
    try:
        for e in sorted(wd.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            tag = "[DIR] " if e.is_dir() else "[FILE]"
            entries.append(f"  {tag} {e.name}")
    except PermissionError:
        entries.append("  [权限不足]")

    return f"当前工作目录: {wd}\n\n目录内容：\n" + "\n".join(entries)

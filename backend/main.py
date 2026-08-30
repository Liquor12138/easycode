"""
FastAPI 服务端
提供 REST API 接口，供前端调用：
  - POST /api/run                提交任务给 Agent（后台执行）
  - GET  /api/task/{task_id}     查询任务状态与结果
  - GET  /api/confirmations/{task_id}  获取待确认的文件修改列表
  - POST /api/confirm/{task_id}/{conf_id}  批准或拒绝文件修改
  - GET  /api/status             查看配置状态与工作目录
  - POST /api/workdir            设置工作目录
  - GET  /api/history            获取最近一次执行的步骤日志
"""

import os
import time
import json
import uuid
import threading
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import Config
from agent import CodingAgent, AgentResult, StepLog
from tools import _resolve_path, _execute_command, _check_command_safety

app = FastAPI(title="Coding Agent Backend", version="0.2.0")

# CORS：允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 全局状态
# ============================================================

_default_workdir = str(Path(__file__).parent.parent.resolve())
_working_dir: str = _default_workdir
_last_result: Optional[dict] = None


# ============================================================
# 任务管理（后台线程执行 + 确认机制）
# ============================================================

@dataclass
class Confirmation:
    """一条待确认的文件修改请求。"""
    id: str
    tool: str
    args: dict
    preview: str
    event: threading.Event = field(default_factory=threading.Event)
    approved: Optional[bool] = None
    reason: str = ""


@dataclass
class TaskState:
    """一个 Agent 任务的完整状态。"""
    id: str
    status: str = "pending"          # pending / running / waiting_confirm / completed / failed
    task_text: str = ""
    working_dir: str = ""
    result: Optional[dict] = None
    step_logs: list = field(default_factory=list)
    confirmations: list = field(default_factory=list)  # List[Confirmation]
    error: str = ""


# 任务存储：task_id -> TaskState
_tasks: dict[str, TaskState] = {}
_tasks_lock = threading.Lock()


def _create_confirmation(task: TaskState, request: dict) -> dict:
    """
    确认回调：在 Agent 线程中被调用，阻塞直到用户响应。
    返回 {"approved": bool, "reason": str}
    """
    conf = Confirmation(
        id=str(uuid.uuid4())[:8],
        tool=request["tool"],
        args=request["args"],
        preview=request["preview"],
    )
    task.confirmations.append(conf)
    task.status = "waiting_confirm"

    # 阻塞等待用户批准/拒绝
    conf.event.wait()

    # 用户已响应，恢复运行状态
    task.status = "running"
    return {"approved": conf.approved, "reason": conf.reason}


def _run_agent_background(task: TaskState):
    """在后台线程中运行 Agent。"""
    try:
        task.status = "running"

        # 计划状态跟踪（从工具调用/结果中提取）
        plan_data = {"title": "", "steps": []}

        def on_step(step: StepLog):
            # 跟踪计划状态：检测 create_plan / update_step 工具调用
            for i, tc in enumerate(step.tool_calls):
                if tc["name"] == "create_plan":
                    try:
                        args = json.loads(tc["args"]) if isinstance(tc["args"], str) else tc["args"]
                        plan_data["title"] = args.get("title", "")
                        plan_data["steps"] = [
                            {"index": idx, "description": s, "status": "pending", "result": ""}
                            for idx, s in enumerate(args.get("steps", []))
                        ]
                    except (json.JSONDecodeError, KeyError):
                        pass
                elif tc["name"] == "update_step" and i < len(step.tool_results):
                    try:
                        args = json.loads(tc["args"]) if isinstance(tc["args"], str) else tc["args"]
                        step_idx = args.get("step_index", -1)
                        result_text = args.get("result", "")
                        if 0 <= step_idx < len(plan_data["steps"]):
                            plan_data["steps"][step_idx]["status"] = "completed"
                            plan_data["steps"][step_idx]["result"] = result_text
                    except (json.JSONDecodeError, KeyError):
                        pass

            task.step_logs.append({
                "step": step.step,
                "content": step.content,
                "tool_calls": step.tool_calls,
                "tool_results": [
                    {
                        "tool": r["tool"],
                        "args_preview": {
                            k: (_truncate(v) if isinstance(v, str) else v)
                            for k, v in r["args"].items()
                        },
                        "result_preview": _truncate(r["result"]),
                        "rejected": r.get("rejected", False),
                    }
                    for r in step.tool_results
                ],
                "duration_ms": step.duration_ms,
            })
            # 同步计划状态到 task
            task.plan = {"title": plan_data["title"], "steps": list(plan_data["steps"])}

        agent = CodingAgent(
            working_dir=task.working_dir,
            on_step=on_step,
            on_confirm=lambda req: _create_confirmation(task, req),
        )
        result = agent.run(task.task_text)

        task.result = {
            "success": result.success,
            "final_answer": result.final_answer,
            "steps": task.step_logs,
            "total_steps": result.total_steps,
            "total_duration_ms": result.total_duration_ms,
            "working_dir": task.working_dir,
            "task": task.task_text,
        }
        task.status = "completed"

        # 同步到全局历史
        global _last_result
        _last_result = task.result

    except Exception as e:
        task.status = "failed"
        task.error = str(e)


# ============================================================
# 请求/响应模型
# ============================================================

class RunRequest(BaseModel):
    task: str
    working_dir: Optional[str] = None


class WorkdirRequest(BaseModel):
    path: str


class ConfirmRequest(BaseModel):
    approved: bool
    reason: str = ""


class TerminalRequest(BaseModel):
    command: str


class CreateProjectRequest(BaseModel):
    parent_path: str
    project_name: str


# ============================================================
# API 端点
# ============================================================

@app.get("/api/status")
def get_status():
    """返回 API 配置状态和当前工作目录。"""
    config_info = Config.is_configured()
    return {
        "api": config_info,
        "working_dir": _working_dir,
        "max_iterations": Config.MAX_ITERATIONS,
        "command_timeout": Config.COMMAND_TIMEOUT,
    }


@app.post("/api/workdir")
def set_workdir(req: WorkdirRequest):
    """设置 Agent 的工作目录。"""
    path = Path(req.path).resolve()
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"目录不存在: {req.path}")
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"路径不是目录: {req.path}")

    global _working_dir
    _working_dir = str(path)
    return {"working_dir": _working_dir, "message": f"工作目录已设置为: {_working_dir}"}


@app.post("/api/run")
def run_agent(req: RunRequest):
    """提交编程任务给 Agent 后台执行，返回 task_id。"""
    if not Config.validate():
        raise HTTPException(
            status_code=400,
            detail="DeepSeek API Key 未配置。请编辑 backend/.env 文件。",
        )
    if not req.task.strip():
        raise HTTPException(status_code=400, detail="任务内容不能为空")

    workdir = _working_dir
    if req.working_dir:
        wd = Path(req.working_dir).resolve()
        if not wd.is_dir():
            raise HTTPException(status_code=400, detail=f"工作目录不存在: {req.working_dir}")
        workdir = str(wd)

    task_id = str(uuid.uuid4())[:8]
    task = TaskState(id=task_id, task_text=req.task, working_dir=workdir)

    with _tasks_lock:
        _tasks[task_id] = task

    # 启动后台线程执行 Agent
    thread = threading.Thread(target=_run_agent_background, args=(task,), daemon=True)
    thread.start()

    return {"task_id": task_id, "status": "pending"}


@app.get("/api/task/{task_id}")
def get_task(task_id: str):
    """查询任务状态与结果。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    resp = {
        "task_id": task.id,
        "status": task.status,
        "task": task.task_text,
        "working_dir": task.working_dir,
        "step_count": len(task.step_logs),
        "step_logs": task.step_logs,
    }
    if task.status == "completed" and task.result:
        resp["result"] = task.result
    if task.status == "failed":
        resp["error"] = task.error
    if task.status == "waiting_confirm":
        # 附带待确认信息
        pending = [
            {"id": c.id, "tool": c.tool, "preview": c.preview, "args": c.args}
            for c in task.confirmations
            if not c.event.is_set()
        ]
        resp["pending_confirmations"] = pending
    # 始终附带计划状态（如果有）
    if hasattr(task, 'plan') and task.plan.get("steps"):
        resp["plan"] = task.plan
    return resp


@app.get("/api/confirmations/{task_id}")
def get_confirmations(task_id: str):
    """获取指定任务的待确认文件修改列表。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    pending = [
        {"id": c.id, "tool": c.tool, "preview": c.preview, "args": c.args}
        for c in task.confirmations
        if not c.event.is_set()
    ]
    return {"task_id": task_id, "pending": pending}


@app.post("/api/confirm/{task_id}/{conf_id}")
def respond_confirmation(task_id: str, conf_id: str, req: ConfirmRequest):
    """批准或拒绝一条文件修改确认。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    # 查找对应的确认请求
    conf = None
    for c in task.confirmations:
        if c.id == conf_id:
            conf = c
            break
    if conf is None:
        raise HTTPException(status_code=404, detail=f"确认请求不存在: {conf_id}")
    if conf.event.is_set():
        raise HTTPException(status_code=400, detail="该确认请求已处理")

    # 设置用户决定并唤醒 Agent 线程
    conf.approved = req.approved
    conf.reason = req.reason
    conf.event.set()

    return {
        "conf_id": conf_id,
        "approved": req.approved,
        "message": "已批准" if req.approved else f"已拒绝：{req.reason}",
    }


@app.get("/api/history")
def get_history():
    """获取最近一次执行的完整日志。"""
    if _last_result is None:
        return {"message": "暂无执行记录"}
    return _last_result


@app.get("/api/files")
def get_file_tree(path: str = "."):
    """获取工作目录下的文件树结构，供前端文件浏览器使用。"""
    try:
        dir_path = _resolve_path(path, _working_dir)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if not dir_path.exists() or not dir_path.is_dir():
        raise HTTPException(status_code=404, detail=f"目录不存在: {path}")

    skip_dirs = {"node_modules", ".git", "__pycache__", ".venv", "venv",
                 ".idea", "dist", "build", ".next", ".cache", "site-packages"}

    def _build_tree(d: Path, depth: int) -> list:
        if depth > 5:
            return []
        items = []
        try:
            entries = sorted(
                d.iterdir(),
                key=lambda x: (not x.is_dir(), x.name.lower()),
            )
        except PermissionError:
            return items
        for entry in entries:
            if entry.is_dir():
                if entry.name in skip_dirs or entry.name.startswith("."):
                    continue
                items.append({
                    "name": entry.name,
                    "type": "directory",
                    "path": str(entry.relative_to(Path(_working_dir).resolve())),
                    "children": _build_tree(entry, depth + 1),
                })
            else:
                items.append({
                    "name": entry.name,
                    "type": "file",
                    "path": str(entry.relative_to(Path(_working_dir).resolve())),
                })
        return items

    return {"path": path, "tree": _build_tree(dir_path, 0)}


@app.get("/api/file/{file_path:path}")
def read_file_content(file_path: str):
    """读取指定文件内容，供前端代码查看器使用。"""
    try:
        fp = _resolve_path(file_path, _working_dir)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    if not fp.exists() or not fp.is_file():
        raise HTTPException(status_code=404, detail=f"文件不存在: {file_path}")

    size = fp.stat().st_size
    if size > Config.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大 ({size} bytes)，超过限制 ({Config.MAX_FILE_SIZE} bytes)",
        )

    # 根据扩展名推断语言
    ext_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".jsx": "javascript", ".tsx": "typescript", ".java": "java",
        ".c": "c", ".cpp": "cpp", ".cc": "cpp", ".h": "c",
        ".html": "html", ".css": "css", ".json": "json",
        ".xml": "xml", ".md": "markdown", ".yaml": "yaml",
        ".yml": "yaml", ".toml": "toml", ".sh": "shell",
        ".bat": "bat", ".sql": "sql", ".txt": "plaintext",
    }
    language = ext_map.get(fp.suffix.lower(), "plaintext")

    try:
        content = fp.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = fp.read_bytes().decode("latin-1")

    return {
        "path": file_path,
        "content": content,
        "language": language,
        "size": size,
    }


@app.post("/api/terminal")
def run_terminal_command(req: TerminalRequest):
    """在前端终端中执行命令。"""
    if not req.command.strip():
        raise HTTPException(status_code=400, detail="命令不能为空")

    try:
        _check_command_safety(req.command)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

    args = {"command": req.command, "cwd": "."}
    try:
        output = _execute_command(args, _working_dir)
        return {"output": output, "exit_code": 0}
    except Exception as e:
        return {"output": str(e), "exit_code": 1}


@app.get("/api/browse")
def browse_directories(path: str = ""):
    """浏览目录结构，供前端文件夹选择器使用。
    path 为空时返回系统盘符（Windows）或根目录（Unix）。
    """
    if not path:
        # 返回顶层入口
        if os.name == "nt":
            import string
            drives = []
            for letter in string.ascii_uppercase:
                d = f"{letter}:\\"
                if Path(d).exists():
                    drives.append({"name": f"{letter}:", "path": d, "type": "drive"})
            return {"current": "", "entries": drives}
        else:
            return {"current": "", "entries": [
                {"name": "/", "path": "/", "type": "directory"}
            ]}

    target = Path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {path}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"不是目录: {path}")

    entries = []
    try:
        for entry in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if entry.is_dir() and not entry.name.startswith("."):
                entries.append({
                    "name": entry.name,
                    "path": str(entry),
                    "type": "directory",
                })
    except PermissionError:
        pass

    return {
        "current": str(target.resolve()),
        "parent": str(target.parent.resolve()) if target.parent != target else "",
        "entries": entries,
    }


@app.post("/api/create-project")
def create_project(req: CreateProjectRequest):
    """在指定路径下创建新项目目录。"""
    parent = Path(req.parent_path)
    if not parent.exists() or not parent.is_dir():
        raise HTTPException(status_code=400, detail=f"父目录不存在: {req.parent_path}")

    # 验证项目名称
    name = req.project_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    if any(c in name for c in r'<>:"/\|?*'):
        raise HTTPException(status_code=400, detail="项目名称包含非法字符")

    project_dir = parent / name
    if project_dir.exists():
        raise HTTPException(status_code=400, detail=f"目录已存在: {project_dir}")

    project_dir.mkdir(parents=True, exist_ok=True)
    return {
        "path": str(project_dir),
        "name": name,
        "message": f"项目已创建: {project_dir}",
    }


@app.get("/api/pick-folder")
def pick_folder():
    """打开系统原生文件夹选择对话框，返回用户选择的路径。"""
    try:
        import tkinter as tk
        from tkinter import filedialog
        # 隐藏主窗口
        root = tk.Tk()
        root.withdraw()
        # 保持对话框在最前
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(
            title="选择文件夹",
            parent=root,
        )
        root.destroy()
        if not path:
            return {"selected": False, "path": ""}
        return {"selected": True, "path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"无法打开文件夹选择器: {e}")


@app.get("/api/health")
def health_check():
    """健康检查端点。"""
    return {"status": "ok", "timestamp": int(time.time())}


# ============================================================
# 辅助函数
# ============================================================

def _truncate(text: str, max_len: int = 500) -> str:
    """截断过长的文本，用于日志展示。"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... [已截断，共 {len(text)} 字符]"


# ============================================================
# 启动入口
# ============================================================

if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("  Coding Agent Backend")
    print("=" * 50)
    status = Config.is_configured()
    if status["configured"]:
        print(f"  API Key: {status['api_key_masked']}")
        print(f"  Model:   {status['model']}")
    else:
        print(f"  ! {status['message']}")
    print(f"  工作目录: {_working_dir}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)

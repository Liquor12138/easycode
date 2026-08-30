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

        def on_step(step: StepLog):
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

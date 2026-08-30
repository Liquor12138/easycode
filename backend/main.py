"""
FastAPI 服务端
提供 REST API 接口，供前端调用：
  - POST /api/run       提交任务给 Agent 执行
  - GET  /api/status    查看配置状态与工作目录
  - POST /api/workdir   设置工作目录
  - GET  /api/history   获取最近一次执行的步骤日志
"""

import os
import time
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import Config
from agent import CodingAgent, AgentResult, StepLog

app = FastAPI(title="Coding Agent Backend", version="0.1.0")

# CORS：允许前端跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发阶段允许所有来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 全局状态
# ============================================================

# 默认工作目录：backend 的上级目录（即项目根目录）
_default_workdir = str(Path(__file__).parent.parent.resolve())
_working_dir: str = _default_workdir
_last_result: Optional[dict] = None


# ============================================================
# 请求/响应模型
# ============================================================

class RunRequest(BaseModel):
    task: str                           # 用户下达的编程任务
    working_dir: Optional[str] = None   # 可选：临时指定工作目录


class RunResponse(BaseModel):
    success: bool
    final_answer: str
    steps: list
    total_steps: int
    total_duration_ms: int


class WorkdirRequest(BaseModel):
    path: str


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


@app.post("/api/run", response_model=RunResponse)
def run_agent(req: RunRequest):
    """提交编程任务给 Agent 执行。"""
    if not Config.validate():
        raise HTTPException(
            status_code=400,
            detail="DeepSeek API Key 未配置。请编辑 backend/.env 文件。",
        )

    if not req.task.strip():
        raise HTTPException(status_code=400, detail="任务内容不能为空")

    # 确定工作目录
    workdir = _working_dir
    if req.working_dir:
        wd = Path(req.working_dir).resolve()
        if not wd.is_dir():
            raise HTTPException(status_code=400, detail=f"工作目录不存在: {req.working_dir}")
        workdir = str(wd)

    # 用于收集实时步骤日志
    step_logs = []

    def on_step(step: StepLog):
        step_logs.append({
            "step": step.step,
            "content": step.content,
            "tool_calls": step.tool_calls,
            "tool_results": [
                {
                    "tool": r["tool"],
                    "args_preview": {k: (_truncate(v) if isinstance(v, str) else v) for k, v in r["args"].items()},
                    "result_preview": _truncate(r["result"]),
                }
                for r in step.tool_results
            ],
            "duration_ms": step.duration_ms,
        })

    # 创建 Agent 并执行
    agent = CodingAgent(working_dir=workdir, on_step=on_step)
    result = agent.run(req.task)

    # 保存结果供 /api/history 查询
    global _last_result
    _last_result = {
        "success": result.success,
        "final_answer": result.final_answer,
        "steps": step_logs,
        "total_steps": result.total_steps,
        "total_duration_ms": result.total_duration_ms,
        "working_dir": workdir,
        "task": req.task,
    }

    return RunResponse(**{
        "success": result.success,
        "final_answer": result.final_answer,
        "steps": step_logs,
        "total_steps": result.total_steps,
        "total_duration_ms": result.total_duration_ms,
    })


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
        print(f"  ⚠ {status['message']}")
    print(f"  工作目录: {_working_dir}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=8000)

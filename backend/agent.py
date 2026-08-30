"""
Agent 核心循环模块
实现与 DeepSeek 模型的交互循环：
  1. 将用户任务 + 对话历史 + 工具定义发送给模型
  2. 解析模型响应：若包含 tool_calls 则执行工具，将结果追加到历史
  3. 重复直到模型给出最终文本回答，或达到迭代上限
"""

import json
import time
from dataclasses import dataclass, field
from typing import Optional, Callable

from openai import OpenAI

from config import Config
from tools import TOOL_SCHEMAS, execute_tool


# ============================================================
# 数据结构
# ============================================================

@dataclass
class StepLog:
    """记录 Agent 每一步的执行日志。"""
    step: int                         # 第几步（从 1 开始）
    role: str                         # "assistant" | "tool" | "system"
    content: str = ""                 # 模型的思考文本 / 工具执行结果
    tool_calls: list = field(default_factory=list)   # 模型发起的工具调用
    tool_results: list = field(default_factory=list) # 工具执行结果
    duration_ms: int = 0              # 本步耗时（毫秒）


@dataclass
class AgentResult:
    """Agent 执行完成后的返回结果。"""
    success: bool
    final_answer: str
    steps: list          # List[StepLog]
    total_steps: int
    total_duration_ms: int


# ============================================================
# System Prompt
# ============================================================

SYSTEM_PROMPT = """你是一个编程智能体（Coding Agent），能够通过工具自主完成编程任务。

你可以使用以下工具：
- read_file: 读取文件内容
- write_file: 创建或覆盖写入文件
- list_directory: 查看目录结构
- execute_command: 执行终端命令

工作原则：
1. 先理解任务需求，必要时先查看项目结构
2. 规划实现步骤，逐步完成
3. 每完成一步，检查结果是否符合预期
4. 遇到错误时分析原因并尝试修复
5. 任务完成后给出清晰的总结

注意：
- 文件路径使用相对路径（相对于工作目录）
- 执行命令前确认当前目录是否正确
- 对于破坏性操作要格外谨慎
- 如果任务不明确，直接说明你的疑问"""


# ============================================================
# Agent 核心类
# ============================================================

class CodingAgent:
    """
    编程智能体：封装对话历史管理、模型调用、工具执行和循环控制。
    不使用任何 Agent 框架，所有逻辑自行实现。
    """

    def __init__(self, working_dir: str, on_step: Optional[Callable] = None):
        """
        Args:
            working_dir: Agent 的工作目录（沙箱根目录）
            on_step:     每完成一步后的回调函数，接收 StepLog，用于实时推送
        """
        if not Config.validate():
            raise RuntimeError(
                "DeepSeek API Key 未配置。请编辑 backend/.env 文件，填入有效的 API Key。"
            )

        self.working_dir = working_dir
        self.on_step = on_step

        # 初始化 OpenAI 客户端（指向 DeepSeek）
        self.client = OpenAI(
            api_key=Config.DEEPSEEK_API_KEY,
            base_url=Config.DEEPSEEK_BASE_URL,
        )

        # 对话历史（OpenAI messages 格式）
        self.messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def run(self, task: str) -> AgentResult:
        """
        执行编程任务的主入口。
        启动 Agent 循环，直到任务完成或达到迭代上限。
        """
        start_time = time.time()
        steps: list[StepLog] = []

        # 将用户任务加入对话历史
        self.messages.append({"role": "user", "content": task})

        for iteration in range(1, Config.MAX_ITERATIONS + 1):
            step_start = time.time()
            step_log = StepLog(step=iteration, role="assistant")

            # ---- 调用模型 ----
            try:
                response = self._call_model()
            except Exception as e:
                step_log.content = f"模型调用失败: {e}"
                step_log.duration_ms = int((time.time() - step_start) * 1000)
                steps.append(step_log)
                if self.on_step:
                    self.on_step(step_log)
                return AgentResult(
                    success=False,
                    final_answer=f"模型调用失败: {e}",
                    steps=steps,
                    total_steps=len(steps),
                    total_duration_ms=int((time.time() - start_time) * 1000),
                )

            choice = response.choices[0]
            message = choice.message

            # ---- 判断响应类型 ----

            # 情况 1: 模型返回了工具调用
            if message.tool_calls:
                step_log.tool_calls = [
                    {"id": tc.id, "name": tc.function.name, "args": tc.function.arguments}
                    for tc in message.tool_calls
                ]

                # 将 assistant 消息（含 tool_calls）加入历史
                self.messages.append({
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                })

                step_log.content = message.content or ""

                # 逐个执行工具
                for tc in message.tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    result = execute_tool(tool_name, tool_args, self.working_dir)
                    step_log.tool_results.append({
                        "tool": tool_name,
                        "args": tool_args,
                        "result": result,
                    })

                    # 将工具结果加入对话历史
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

                step_log.duration_ms = int((time.time() - step_start) * 1000)
                steps.append(step_log)
                if self.on_step:
                    self.on_step(step_log)
                # 继续循环，让模型看到工具结果后继续决策

            # 情况 2: 模型返回了纯文本（最终回答）
            else:
                final_text = message.content or "(模型未返回内容)"
                step_log.content = final_text
                step_log.duration_ms = int((time.time() - step_start) * 1000)
                steps.append(step_log)
                if self.on_step:
                    self.on_step(step_log)

                self.messages.append({"role": "assistant", "content": final_text})

                total_ms = int((time.time() - start_time) * 1000)
                return AgentResult(
                    success=True,
                    final_answer=final_text,
                    steps=steps,
                    total_steps=len(steps),
                    total_duration_ms=total_ms,
                )

        # ---- 达到迭代上限 ----
        total_ms = int((time.time() - start_time) * 1000)
        warning = f"已达到最大迭代次数 ({Config.MAX_ITERATIONS})，强制终止。"
        return AgentResult(
            success=False,
            final_answer=warning,
            steps=steps,
            total_steps=len(steps),
            total_duration_ms=total_ms,
        )

    def _call_model(self):
        """调用 DeepSeek 模型，携带工具定义。"""
        return self.client.chat.completions.create(
            model=Config.DEEPSEEK_MODEL,
            messages=self.messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
        )

    def reset(self):
        """重置对话历史，保留 system prompt。"""
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

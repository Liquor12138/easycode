"""
Agent 核心循环模块
实现与 DeepSeek 模型的交互循环：
  1. system message（角色 + 规范）+ user message（需求）
  2. 模型返回 assistant message（含 tool_calls）
  3. 本地执行工具，将 tool message（结果 + tool_call_id）追加到历史
  4. 重复 2-3 直到模型调用 finish_task 或达到迭代上限
"""

import json
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Callable

from openai import OpenAI

from config import Config
from tools import TOOL_SCHEMAS, execute_tool

# 重复检测：连续 N 次调用相同工具（相同参数）时注入提醒
_REPEAT_THRESHOLD = 5
# 重复检测：连续 N 次调用相同工具名（不论参数）时注入提醒
_REPEAT_TOOL_THRESHOLD = 8


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

## 可用工具

文件读写：
- read_file(path): 读取文件内容
- write_file(path, content): 创建或覆盖写入文件
- search_replace(path, old_text, new_text): 精确替换文件中的文本片段

目录与终端：
- list_directory(path): 查看目录结构
- get_workdir(): 查看当前工作目录路径
- execute_command(command): 执行终端命令

代码分析：
- search_text(pattern, path): 搜索文本/正则，返回匹配行号
- get_diagnostics(path): 获取编译器/linter 报错信息
- list_symbols(path): 列出文件中的函数、类、变量等符号

计划管理：
- create_plan(title, steps): 制定分步计划
- update_step(step_index, result): 标记某步骤已完成
- finish_task(summary): 提交总结并结束任务

## 工作流程（必须严格遵守，不可跳过任何步骤）

1. **制定计划（第一步，必须最先执行）**：收到任何任务后，第一件事必须是调用 create_plan 制定分步计划。不允许在制定计划之前调用任何其他工具（read_file、list_directory 等都不允许）
2. **逐步执行**：按计划顺序执行每一步，每完成一步立即调用 update_step 标记
3. **实际测试**：编写或修改代码后，必须使用 execute_command 工具实际运行代码来验证正确性。不能仅依赖 get_diagnostics 等静态检查工具。如果 execute_command 执行失败，应尝试诊断原因并重试，而不是放弃测试
4. **完成总结**：所有步骤完成后，调用 finish_task 提交最终总结，结束任务

## 规范

- 文件路径使用相对路径（相对于工作目录）
- 修改文件时优先使用 search_replace 进行精确修改，避免重写整个文件
- 遇到编译错误时用 get_diagnostics 检查并修复
- 编写代码后必须使用 execute_command 实际运行测试，不要仅凭静态分析就断言代码正确或环境不可用
- 如果任务不明确，直接说明你的疑问"""


# ============================================================
# Agent 核心类
# ============================================================

# 需要用户确认的文件修改工具
_FILE_MODIFY_TOOLS = {"write_file", "search_replace"}


class CodingAgent:
    """
    编程智能体：封装对话历史管理、模型调用、工具执行和循环控制。
    不使用任何 Agent 框架，所有逻辑自行实现。
    """

    def __init__(
        self,
        working_dir: str,
        on_step: Optional[Callable] = None,
        on_confirm: Optional[Callable] = None,
        cancel_event: Optional[threading.Event] = None,
    ):
        """
        Args:
            working_dir: Agent 的工作目录（沙箱根目录）
            on_step:     每完成一步后的回调函数，接收 StepLog，用于实时推送
            on_confirm:  文件修改确认回调，接收 dict(tool, args, preview)，
                         返回 dict(approved: bool)。若为 None 则自动批准。
            cancel_event: 取消信号，外部通过 set() 通知 Agent 停止。
        """
        if not Config.validate():
            raise RuntimeError(
                "DeepSeek API Key 未配置。请编辑 backend/.env 文件，填入有效的 API Key。"
            )

        self.working_dir = working_dir
        self.on_step = on_step
        self.on_confirm = on_confirm
        self.cancel_event = cancel_event

        # 计划状态（由 create_plan / update_step / finish_task 工具读写）
        self.plan_state: dict = {}

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

        # 重置计划状态
        self.plan_state = {}

        # 将用户任务加入对话历史
        self.messages.append({"role": "user", "content": task})

        for iteration in range(1, Config.MAX_ITERATIONS + 1):
            # ---- 取消检查 ----
            if self.cancel_event and self.cancel_event.is_set():
                total_ms = int((time.time() - start_time) * 1000)
                return AgentResult(
                    success=False,
                    final_answer="用户已手动停止任务。",
                    steps=steps,
                    total_steps=len(steps),
                    total_duration_ms=total_ms,
                )

            # ---- 上下文压缩：消息过多时压缩早期历史 ----
            self._compress_history()

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

                # ---- 重复检测：检测 Agent 是否陷入死循环 ----
                nudge = self._check_repetition(step_log.tool_calls)

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
                finish_called = False
                for tc in message.tool_calls:
                    tool_name = tc.function.name
                    try:
                        tool_args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        tool_args = {}

                    # ---- 文件修改工具需要用户确认 ----
                    if tool_name in _FILE_MODIFY_TOOLS and self.on_confirm:
                        preview = self._build_confirm_preview(tool_name, tool_args)
                        decision = self.on_confirm({
                            "tool": tool_name,
                            "args": tool_args,
                            "preview": preview,
                        })
                        if not decision.get("approved", False):
                            reason = decision.get("reason", "用户拒绝")
                            result = f"用户已拒绝此修改：{reason}"
                            step_log.tool_results.append({
                                "tool": tool_name,
                                "args": tool_args,
                                "result": result,
                                "rejected": True,
                            })
                            self.messages.append({
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result,
                            })
                            continue  # 跳过执行，处理下一个工具调用

                    result = execute_tool(
                        tool_name, tool_args, self.working_dir, self.plan_state
                    )
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

                    # 检测 finish_task：模型主动结束任务
                    if tool_name == "finish_task":
                        finish_called = True

                # 如果检测到重复调用，注入提醒消息
                if nudge:
                    self.messages.append({
                        "role": "user",
                        "content": nudge,
                    })

                step_log.duration_ms = int((time.time() - step_start) * 1000)
                steps.append(step_log)
                if self.on_step:
                    self.on_step(step_log)

                # 如果模型调用了 finish_task，任务完成
                if finish_called:
                    summary = self.plan_state.get("summary", result)
                    total_ms = int((time.time() - start_time) * 1000)
                    return AgentResult(
                        success=True,
                        final_answer=summary,
                        steps=steps,
                        total_steps=len(steps),
                        total_duration_ms=total_ms,
                    )
                # 否则继续循环，让模型看到工具结果后继续决策

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

    def _compress_history(self):
        """
        上下文压缩：当消息数超过阈值时，将最早的若干条可压缩消息合并为一条 history 消息。

        规则：
        - system 和 user 消息永不压缩
        - history 消息默认不压缩，除非最早的可压缩消息全是 history
        - 每次压缩 COMPRESS_COUNT 条消息
        """
        if len(self.messages) <= Config.COMPRESS_THRESHOLD:
            return

        n = Config.COMPRESS_COUNT

        # 跳过 system 消息（始终保留在 index 0）
        start = 1 if self.messages and self.messages[0]["role"] == "system" else 0

        # 从 start 开始扫描，跳过 user 消息，收集前 n 条可压缩消息
        compressible_indices = []
        for i in range(start, len(self.messages)):
            if self.messages[i]["role"] == "user":
                continue
            compressible_indices.append(i)
            if len(compressible_indices) == n:
                break

        # 不足 n 条可压缩消息，无需压缩
        if len(compressible_indices) < n:
            return

        # 检查是否全是 history 消息
        all_history = all(
            self.messages[i]["role"] == "history" for i in compressible_indices
        )
        if not all_history:
            return  # 含 assistant/tool 消息，暂不压缩

        # ---- 执行压缩 ----
        compressible_msgs = [self.messages[i] for i in compressible_indices]

        # 构建压缩摘要
        summary_parts = []
        for msg in compressible_msgs:
            content = msg.get("content", "")
            if content:
                summary_parts.append(content)

        merged_content = "\n---\n".join(summary_parts)
        history_msg = {
            "role": "history",
            "content": f"[历史压缩] 以下是之前已完成工作的记录摘要：\n\n{merged_content}",
        }

        # 重建消息列表：
        # system + 原有 user 消息 + 压缩后的 history + 剩余消息
        first_idx = compressible_indices[0]
        last_idx = compressible_indices[-1]

        # 被压缩块之前的 user 消息（保留）
        pre_user_msgs = [
            self.messages[i] for i in range(start, first_idx)
            if self.messages[i]["role"] == "user"
        ]

        # 被压缩块之后的所有消息（保留）
        remaining_msgs = [self.messages[i] for i in range(last_idx + 1, len(self.messages))]

        # 重建：system + pre_user + history + remaining
        self.messages = [self.messages[0]] + pre_user_msgs + [history_msg] + remaining_msgs

    @staticmethod
    def _build_confirm_preview(tool_name: str, args: dict) -> str:
        """构建文件修改的预览信息，供用户确认。"""
        if tool_name == "write_file":
            path = args.get("path", "?")
            content = args.get("content", "")
            lines = content.count("\n") + 1
            return f"写入文件: {path} ({len(content)} 字符, {lines} 行)"
        elif tool_name == "search_replace":
            path = args.get("path", "?")
            old = args.get("old_text", "")
            new = args.get("new_text", "")
            preview = f"修改文件: {path}\n"
            preview += f"- 原文 ({len(old)} 字符):\n{old[:200]}\n"
            preview += f"+ 替换为 ({len(new)} 字符):\n{new[:200]}"
            return preview
        return f"{tool_name}: {args}"

    def _check_repetition(self, tool_calls: list) -> str:
        """
        检测 Agent 是否陷入重复调用的死循环。
        如果最近连续多次调用相同的工具（相同名称+参数）或相同工具名，
        返回一段提醒文本供注入对话历史；否则返回空字符串。
        """
        if not tool_calls:
            return ""

        # 从对话历史中提取最近若干轮的工具调用记录
        recent_calls = []  # (tool_name, arguments_str)
        recent_tool_names = []  # tool_name only
        for msg in reversed(self.messages):
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    recent_calls.append((fn.get("name", ""), fn.get("arguments", "")))
                    recent_tool_names.append(fn.get("name", ""))
                if len(recent_calls) >= max(_REPEAT_THRESHOLD, _REPEAT_TOOL_THRESHOLD) + 2:
                    break

        # 检查 1：完全相同的调用（工具名+参数都一样）
        if len(recent_calls) >= _REPEAT_THRESHOLD:
            current = (tool_calls[0].function.name, tool_calls[0].function.arguments)
            if all(c == current for c in recent_calls[:_REPEAT_THRESHOLD]):
                return (
                    "⚠️ 系统检测：你最近连续多次执行了完全相同的工具调用，陷入了重复循环。"
                    "请立即停止当前操作，换一种方式推进任务，或者直接跳到计划的下一个步骤。"
                    "不要再次调用相同的工具。"
                )

        # 检查 2：相同工具名（参数不同但工具一样，如反复 read_file 同一个文件）
        if len(recent_tool_names) >= _REPEAT_TOOL_THRESHOLD:
            current_name = tool_calls[0].function.name
            if all(n == current_name for n in recent_tool_names[:_REPEAT_TOOL_THRESHOLD]):
                return (
                    f"⚠️ 系统检测：你已连续 {_REPEAT_TOOL_THRESHOLD} 次调用 `{current_name}` 工具，"
                    "但没有取得实质进展。请停止重复，换一种方法或直接推进到计划的下一步。"
                    "如果你已经获取了足够信息，请立即开始编写代码。"
                )

        return ""

    def reset(self):
        """重置对话历史和计划状态。"""
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.plan_state = {}

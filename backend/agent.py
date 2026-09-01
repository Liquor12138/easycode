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
_REPEAT_THRESHOLD = 3
# 重复检测：连续 N 次调用相同工具名（不论参数）时注入提醒
_REPEAT_TOOL_THRESHOLD = 5


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
        上下文压缩：当消息数超过 30 条时，将前 5 条可压缩消息发送给 LLM 总结为一条 history。

        策略：
        - system 和 user 消息永不压缩
        - 扫描消息队列前方（跳过 system/user）的前 5 条可压缩消息
        - 如果前方有 >= 5 条 history → 压缩这 5 条 history
        - 如果前方 history 不足 5 条 → 不压缩 history，改为压缩紧随其后的 5 条 assistant/tool 消息
        - 压缩方式：发送给 LLM，让其总结这些消息完成了什么工作、有什么结果
        """
        if len(self.messages) <= Config.COMPRESS_THRESHOLD:
            return

        n = Config.COMPRESS_COUNT  # 5
        start = 1 if self.messages and self.messages[0]["role"] == "system" else 0

        # 收集前方连续的可压缩消息（非 system/user），并计算其中 history 数量
        compressible_indices = []
        history_count = 0
        for i in range(start, len(self.messages)):
            role = self.messages[i]["role"]
            if role in ("system", "user"):
                break  # 遇到不可压缩消息就停止，只处理最前面的连续块
            compressible_indices.append(i)
            if role == "history":
                history_count += 1
            if len(compressible_indices) >= n:
                break

        if len(compressible_indices) < n:
            # 前方连续可压缩消息不足 5 条，不压缩
            return

        # 决定压缩目标
        if history_count >= n:
            # 前方有 >= 5 条 history，压缩前 5 条 history
            target_indices = compressible_indices[:n]
        else:
            # 前方 history 不足 5 条，不压缩 history
            # 改为找 history 后面的（或混合块中的）5 条 assistant/tool 消息
            assistant_tool_indices = [
                i for i in compressible_indices
                if self.messages[i]["role"] in ("assistant", "tool")
            ]
            if len(assistant_tool_indices) < n:
                # 不足 5 条 assistant/tool，不压缩
                return
            target_indices = assistant_tool_indices[:n]

        # ---- 构建发送给 LLM 的内容 ----
        target_msgs = [self.messages[i] for i in target_indices]
        summary_content = self._summarize_with_llm(target_msgs)
        if not summary_content:
            return  # LLM 总结失败，跳过本次压缩

        history_msg = {
            "role": "history",
            "content": summary_content,
        }

        # 重建消息列表：保留 target 之前的消息 + history 摘要 + target 之后的消息
        first_idx = target_indices[0]
        last_idx = target_indices[-1]

        before = self.messages[:first_idx]
        after = self.messages[last_idx + 1:]
        self.messages = before + [history_msg] + after

    def _summarize_with_llm(self, messages: list) -> str:
        """
        将一组消息发送给 LLM，让其总结这些消息完成了什么工作、有什么结果。
        返回总结文本；失败时返回空字符串。
        """
        # 构建可读的消息摘要（截断过长内容以控制 token）
        parts = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "history":
                if content:
                    parts.append(f"[历史记录] {content[:500]}")
            elif role == "assistant":
                if content:
                    parts.append(f"[助手] {content[:300]}")
                for tc in msg.get("tool_calls", []):
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args_str = fn.get("arguments", "")
                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                        key_info = {k: str(v)[:100] for k, v in (args.items() if isinstance(args, dict) else [])}
                        parts.append(f"[调用工具] {name}({key_info})")
                    except (json.JSONDecodeError, AttributeError):
                        parts.append(f"[调用工具] {name}")
            elif role == "tool":
                if content:
                    parts.append(f"[工具结果] {content[:300]}")
            else:
                if content:
                    parts.append(f"[{role}] {content[:300]}")

        if not parts:
            return ""

        text_to_summarize = "\n".join(parts)

        try:
            response = self.client.chat.completions.create(
                model=Config.DEEPSEEK_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是一个对话历史压缩助手。你会收到一段编程智能体的对话记录片段，"
                            "请用简洁的中文总结这些消息完成了什么工作、得到了什么关键结果、修改了哪些文件。"
                            "总结控制在 200 字以内，不要包含代码细节。"
                            "直接输出总结内容，不要加任何前缀或格式标记。"
                        ),
                    },
                    {"role": "user", "content": text_to_summarize},
                ],
                max_tokens=300,
            )
            summary = response.choices[0].message.content.strip()
            if summary:
                return f"[LLM 总结] {summary}"
        except Exception as e:
            print(f"[压缩] LLM 总结失败: {e}")

        return ""

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
            current = (tool_calls[0]["name"], tool_calls[0]["args"])
            if all(c == current for c in recent_calls[:_REPEAT_THRESHOLD]):
                return (
                    "⚠️ 系统检测：你最近连续多次执行了完全相同的工具调用，陷入了重复循环。"
                    "请立即停止当前操作，换一种方式推进任务，或者直接跳到计划的下一个步骤。"
                    "不要再次调用相同的工具。"
                )

        # 检查 2：相同工具名（参数不同但工具一样，如反复 read_file 同一个文件）
        if len(recent_tool_names) >= _REPEAT_TOOL_THRESHOLD:
            current_name = tool_calls[0]["name"]
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

// ============================================================
// 文件树
// ============================================================

export interface FileNode {
  name: string;
  type: 'file' | 'directory';
  path: string;
  children?: FileNode[];
}

// ============================================================
// 文件内容
// ============================================================

export interface FileContent {
  path: string;
  content: string;
  language: string;
  size: number;
}

// ============================================================
// 任务 & Agent
// ============================================================

export type TaskStatus = 'pending' | 'running' | 'waiting_confirm' | 'completed' | 'failed' | 'stopped';

export interface ToolCallInfo {
  id: string;
  name: string;
  args: string; // JSON string
}

export interface ToolResultInfo {
  tool: string;
  args_preview: Record<string, string>;
  result_preview: string;
  rejected?: boolean;
}

export interface StepLogInfo {
  step: number;
  content: string;
  tool_calls: ToolCallInfo[];
  tool_results: ToolResultInfo[];
  duration_ms: number;
}

export interface TaskState {
  task_id: string;
  status: TaskStatus;
  task: string;
  working_dir: string;
  step_count: number;
  step_logs?: StepLogInfo[];
  plan?: PlanState;
  result?: TaskResult;
  error?: string;
  pending_confirmations?: ConfirmationInfo[];
}

export interface PlanState {
  title: string;
  steps: PlanStep[];
}

export interface TaskResult {
  success: boolean;
  final_answer: string;
  steps: StepLogInfo[];
  total_steps: number;
  total_duration_ms: number;
  working_dir: string;
  task: string;
}

// ============================================================
// 确认
// ============================================================

export interface ConfirmationInfo {
  id: string;
  tool: string;
  preview: string;
  args: Record<string, unknown>;
}

// ============================================================
// 聊天消息（前端展示用）
// ============================================================

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool_call' | 'plan' | 'result';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  timestamp: number;
  // 工具调用专用
  toolName?: string;
  toolArgs?: Record<string, unknown>;
  toolResult?: string;
  rejected?: boolean;
  // 计划专用
  planTitle?: string;
  planSteps?: PlanStep[];
}

export interface PlanStep {
  index: number;
  description: string;
  status: 'pending' | 'completed';
  result?: string;
}

// ============================================================
// 终端
// ============================================================

export interface TerminalEntry {
  id: string;
  command: string;
  output: string;
  exitCode: number;
  timestamp: number;
}

// ============================================================
// API 状态
// ============================================================

export interface ApiStatus {
  api: {
    configured: boolean;
    message?: string;
    api_key_masked?: string;
    model?: string;
    base_url?: string;
  };
  working_dir: string;
  max_iterations: number;
  command_timeout: number;
}

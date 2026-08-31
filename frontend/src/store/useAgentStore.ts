import { create } from 'zustand';
import type {
  FileNode,
  ChatMessage,
  TaskStatus,
  ConfirmationInfo,
  TerminalEntry,
  StepLogInfo,
  PlanState,
} from '../types';
import * as api from '../api/client';

// ============================================================
// Store 接口
// ============================================================

interface AgentStore {
  // ---- 项目状态 ----
  projectSelected: boolean;
  workingDir: string;
  fileTree: FileNode[];
  loadingFiles: boolean;

  // ---- 文件查看 ----
  openFiles: Map<string, { content: string; language: string }>;
  activeFile: string | null;

  // ---- 对话 & 任务 ----
  messages: ChatMessage[];
  taskId: string | null;
  agentStatus: TaskStatus | 'idle';
  stepLogs: StepLogInfo[];
  pendingConfirmations: ConfirmationInfo[];

  // ---- 终端 ----
  terminalEntries: TerminalEntry[];
  terminalOpen: boolean;

  // ---- 高亮 ----
  highlightLines: { start: number; end: number } | null;

  // ---- 错误 ----
  error: string | null;

  // ---- 计划 ----
  plan: PlanState | null;

  // ---- Actions ----
  initStatus: () => Promise<void>;
  openProject: (path: string) => Promise<void>;
  createAndOpenProject: (parentPath: string, projectName: string) => Promise<void>;
  resetToWelcome: () => void;
  loadFileTree: () => Promise<void>;
  openFile: (path: string) => Promise<void>;
  closeFile: (path: string) => void;
  setActiveFile: (path: string | null) => void;

  sendMessage: (text: string) => Promise<void>;
  pollTaskStatus: () => void;
  stopPolling: () => void;
  stopTask: () => Promise<void>;

  confirmChange: (confId: string, approved: boolean, reason?: string) => Promise<void>;

  executeCommand: (command: string) => Promise<void>;
  toggleTerminal: () => void;

  clearError: () => void;
  setHighlightLines: (lines: { start: number; end: number } | null) => void;
}

// ============================================================
// 轮询定时器
// ============================================================

let pollTimer: ReturnType<typeof setInterval> | null = null;
let processedStepCount = 0;

// ============================================================
// Store 实现
// ============================================================

export const useAgentStore = create<AgentStore>((set, get) => ({
  // ---- 初始状态 ----
  projectSelected: false,
  workingDir: '',
  fileTree: [],
  loadingFiles: false,
  openFiles: new Map(),
  activeFile: null,
  messages: [],
  taskId: null,
  agentStatus: 'idle',
  stepLogs: [],
  pendingConfirmations: [],
  terminalEntries: [],
  terminalOpen: false,
  error: null,
  plan: null,
  highlightLines: null,

  // ============================================================
  // 初始化：仅检测后端连接，不加载文件树
  // ============================================================
  initStatus: async () => {
    try {
      const status = await api.fetchStatus();
      set({ workingDir: status.working_dir });
    } catch (e: unknown) {
      set({ error: `无法连接后端: ${(e as Error).message}` });
    }
  },

  // ============================================================
  // 打开项目：设置工作目录并加载文件树
  // ============================================================
  openProject: async (path: string) => {
    try {
      await api.setWorkdir(path);
      set({ workingDir: path, projectSelected: true, error: null });
      await get().loadFileTree();
    } catch (e: unknown) {
      set({ error: `打开项目失败: ${(e as Error).message}` });
    }
  },

  // ============================================================
  // 创建并打开项目
  // ============================================================
  createAndOpenProject: async (parentPath: string, projectName: string) => {
    try {
      const res = await api.createProject(parentPath, projectName);
      await api.setWorkdir(res.path);
      set({ workingDir: res.path, projectSelected: true, error: null });
      await get().loadFileTree();
    } catch (e: unknown) {
      set({ error: `创建项目失败: ${(e as Error).message}` });
    }
  },

  // ============================================================
  // 重置到欢迎页：清除所有状态
  // ============================================================
  resetToWelcome: () => {
    get().stopPolling();
    set({
      projectSelected: false,
      fileTree: [],
      openFiles: new Map(),
      activeFile: null,
      messages: [],
      taskId: null,
      agentStatus: 'idle',
      stepLogs: [],
      pendingConfirmations: [],
      terminalEntries: [],
      terminalOpen: false,
      highlightLines: null,
      error: null,
      plan: null,
    });
  },

  // ============================================================
  // 加载文件树
  // ============================================================
  loadFileTree: async () => {
    set({ loadingFiles: true });
    try {
      const res = await api.fetchFileTree();
      set({ fileTree: res.tree, loadingFiles: false });
    } catch (e: unknown) {
      set({ loadingFiles: false, error: `加载文件树失败: ${(e as Error).message}` });
    }
  },

  // ============================================================
  // 打开文件
  // ============================================================
  openFile: async (path: string) => {
    const { openFiles } = get();
    if (openFiles.has(path)) {
      set({ activeFile: path });
      return;
    }
    try {
      const file = await api.fetchFileContent(path);
      const newMap = new Map(openFiles);
      newMap.set(path, { content: file.content, language: file.language });
      set({ openFiles: newMap, activeFile: path });
    } catch (e: unknown) {
      set({ error: `无法打开文件: ${(e as Error).message}` });
    }
  },

  closeFile: (path: string) => {
    const { openFiles, activeFile } = get();
    const newMap = new Map(openFiles);
    newMap.delete(path);
    const keys = Array.from(newMap.keys());
    set({
      openFiles: newMap,
      activeFile: activeFile === path ? (keys.length > 0 ? keys[keys.length - 1] : null) : activeFile,
    });
  },

  setActiveFile: (path: string | null) => set({ activeFile: path }),

  // ============================================================
  // 发送消息 → 启动任务
  // ============================================================
  sendMessage: async (text: string) => {
    const { workingDir, messages } = get();

    // 添加用户消息
    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: Date.now(),
    };
    set({ messages: [...messages, userMsg], error: null, agentStatus: 'pending', stepLogs: [] });

    try {
      const res = await api.runTask(text, workingDir);
      set({ taskId: res.task_id, agentStatus: 'running' });
      processedStepCount = 0;

      // 开始轮询
      get().stopPolling();
      get().pollTaskStatus();
    } catch (e: unknown) {
      set({ error: `任务启动失败: ${(e as Error).message}`, agentStatus: 'idle' });
    }
  },

  // ============================================================
  // 轮询任务状态
  // ============================================================
  pollTaskStatus: () => {
    if (pollTimer) clearInterval(pollTimer);

    pollTimer = setInterval(async () => {
      const { taskId, messages } = get();
      if (!taskId) return;

      try {
        const task = await api.fetchTask(taskId);
        const newMessages = [...messages];

        // 处理新的 step logs：为每个工具调用生成消息
        const stepLogs = task.step_logs || [];
        if (stepLogs.length > processedStepCount) {
          for (let i = processedStepCount; i < stepLogs.length; i++) {
            const step = stepLogs[i];
            for (const tc of step.tool_calls) {
              // 文件修改工具已通过确认流程显示，跳过重复
              if (tc.name === 'write_file' || tc.name === 'search_replace') {
                continue;
              }
              // 跳过计划管理工具（不单独显示）
              if (['create_plan', 'update_step', 'finish_task'].includes(tc.name)) {
                continue;
              }
              let args: Record<string, unknown> = {};
              try {
                args = JSON.parse(tc.args);
              } catch { /* ignore */ }
              const resultInfo = step.tool_results?.find((r) => r.tool === tc.name);
              newMessages.push({
                id: `tc-${tc.id}`,
                role: 'tool_call',
                content: resultInfo?.result_preview || tc.name,
                timestamp: Date.now(),
                toolName: tc.name,
                toolArgs: args,
                toolResult: resultInfo?.result_preview,
              });
            }
          }
          processedStepCount = stepLogs.length;
        }

        // 更新计划状态
        if (task.plan && task.plan.steps && task.plan.steps.length > 0) {
          set({ plan: task.plan });
        }

        // 处理确认请求
        if (task.status === 'waiting_confirm' && task.pending_confirmations) {
          const existing = get().pendingConfirmations;
          const newConfs = task.pending_confirmations.filter(
            (c) => !existing.some((e) => e.id === c.id),
          );
          for (const conf of newConfs) {
            newMessages.push({
              id: `conf-${conf.id}`,
              role: 'tool_call',
              content: conf.preview,
              timestamp: Date.now(),
              toolName: conf.tool,
              toolArgs: conf.args,
            });

            // 自动打开被修改的文件并计算高亮行范围
            const filePath = String(conf.args.path || conf.args.file_path || '');
            if (filePath) {
              await get().openFile(filePath);
              const state = get();
              const fileData = state.openFiles.get(filePath);
              let lines: { start: number; end: number } | null = null;

              if (conf.tool === 'search_replace' && fileData) {
                const oldText = (conf.args.old_text as string) || '';
                if (oldText) {
                  const idx = fileData.content.indexOf(oldText);
                  if (idx >= 0) {
                    const start = fileData.content.substring(0, idx).split('\n').length;
                    const end = start + oldText.split('\n').length - 1;
                    lines = { start, end };
                  }
                }
              } else if (conf.tool === 'write_file') {
                const newContent = (conf.args.content as string) || '';
                if (newContent && (!fileData || !fileData.content)) {
                  // 新文件：高亮全部行
                  lines = { start: 1, end: newContent.split('\n').length };
                }
              }

              set({ highlightLines: lines });
            }
          }
          set({
            agentStatus: 'waiting_confirm',
            pendingConfirmations: [...existing, ...newConfs],
            messages: newMessages,
          });
        } else if (task.status !== get().agentStatus) {
          set({ agentStatus: task.status, messages: newMessages });
        } else if (newMessages.length > messages.length) {
          set({ messages: newMessages });
        }

        // 任务完成
        if (task.status === 'completed' && task.result) {
          newMessages.push({
            id: `result-${Date.now()}`,
            role: 'result',
            content: task.result.final_answer,
            timestamp: Date.now(),
          });
          set({ messages: newMessages, agentStatus: 'completed' });
          get().stopPolling();
          // 刷新文件树
          await get().loadFileTree();
        }

        // 任务失败
        if (task.status === 'failed') {
          newMessages.push({
            id: `error-${Date.now()}`,
            role: 'system',
            content: `任务失败: ${task.error || '未知错误'}`,
            timestamp: Date.now(),
          });
          set({ messages: newMessages, agentStatus: 'failed' });
          get().stopPolling();
        }

        // 任务被停止
        if (task.status === 'stopped') {
          newMessages.push({
            id: `stopped-${Date.now()}`,
            role: 'system',
            content: '任务已被手动停止。',
            timestamp: Date.now(),
          });
          set({ messages: newMessages, agentStatus: 'stopped' });
          get().stopPolling();
        }
      } catch {
        // 轮询失败时静默重试
      }
    }, 1000);
  },

  stopPolling: () => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  },

  // ============================================================
  // 停止任务
  // ============================================================
  stopTask: async () => {
    const { taskId } = get();
    if (!taskId) return;

    try {
      await api.stopTask(taskId);
      get().stopPolling();
      set({ agentStatus: 'stopped' });
    } catch (e: unknown) {
      set({ error: `停止任务失败: ${(e as Error).message}` });
    }
  },

  // ============================================================
  // 确认/拒绝文件修改
  // ============================================================
  confirmChange: async (confId: string, approved: boolean, reason = '') => {
    const { taskId, pendingConfirmations, messages } = get();
    if (!taskId) return;

    try {
      await api.respondConfirmation(taskId, confId, approved, reason);

      // 更新消息状态
      const updatedMsgs = messages.map((m) => {
        if (m.id === `conf-${confId}`) {
          return { ...m, rejected: !approved, toolResult: approved ? '已接受' : `已拒绝: ${reason}` };
        }
        return m;
      });

      // 移除已处理的确认
      const newConfs = pendingConfirmations.filter((c) => c.id !== confId);

      set({
        messages: updatedMsgs,
        pendingConfirmations: newConfs,
        agentStatus: newConfs.length > 0 ? 'waiting_confirm' : 'running',
      });
    } catch (e: unknown) {
      set({ error: `确认操作失败: ${(e as Error).message}` });
    }
  },

  // ============================================================
  // 终端命令
  // ============================================================
  executeCommand: async (command: string) => {
    const { terminalEntries } = get();
    try {
      const res = await api.executeTerminalCommand(command);
      const entry: TerminalEntry = {
        id: `term-${Date.now()}`,
        command,
        output: res.output,
        exitCode: res.exit_code,
        timestamp: Date.now(),
      };
      set({ terminalEntries: [...terminalEntries, entry] });
    } catch (e: unknown) {
      const entry: TerminalEntry = {
        id: `term-${Date.now()}`,
        command,
        output: `执行失败: ${(e as Error).message}`,
        exitCode: 1,
        timestamp: Date.now(),
      };
      set({ terminalEntries: [...terminalEntries, entry] });
    }
  },

  toggleTerminal: () => set((s) => ({ terminalOpen: !s.terminalOpen })),

  clearError: () => set({ error: null }),

  setHighlightLines: (lines) => set({ highlightLines: lines }),
}));

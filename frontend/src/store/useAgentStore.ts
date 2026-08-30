import { create } from 'zustand';
import type {
  FileNode,
  ChatMessage,
  TaskStatus,
  ConfirmationInfo,
  TerminalEntry,
  StepLogInfo,
} from '../types';
import * as api from '../api/client';

// ============================================================
// Store 接口
// ============================================================

interface AgentStore {
  // ---- 工作目录 & 文件树 ----
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

  // ---- 错误 ----
  error: string | null;

  // ---- Actions ----
  initStatus: () => Promise<void>;
  loadFileTree: () => Promise<void>;
  openFile: (path: string) => Promise<void>;
  closeFile: (path: string) => void;
  setActiveFile: (path: string | null) => void;

  sendMessage: (text: string) => Promise<void>;
  pollTaskStatus: () => void;
  stopPolling: () => void;

  confirmChange: (confId: string, approved: boolean, reason?: string) => Promise<void>;

  executeCommand: (command: string) => Promise<void>;
  toggleTerminal: () => void;

  clearError: () => void;
}

// ============================================================
// 轮询定时器
// ============================================================

let pollTimer: ReturnType<typeof setInterval> | null = null;
let lastStepCount = 0;

// ============================================================
// Store 实现
// ============================================================

export const useAgentStore = create<AgentStore>((set, get) => ({
  // ---- 初始状态 ----
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

  // ============================================================
  // 初始化：获取后端状态
  // ============================================================
  initStatus: async () => {
    try {
      const status = await api.fetchStatus();
      set({ workingDir: status.working_dir });
      await get().loadFileTree();
    } catch (e: unknown) {
      set({ error: `无法连接后端: ${(e as Error).message}` });
    }
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
      lastStepCount = 0;

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
      const { taskId, messages, stepLogs } = get();
      if (!taskId) return;

      try {
        const task = await api.fetchTask(taskId);
        const newMessages = [...messages];
        const newStepLogs = [...stepLogs];

        // 处理新的 step logs
        if (task.step_count > lastStepCount) {
          // 通过 task API 获取不到完整 step logs，用 confirmations 判断
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
          }
          set({
            agentStatus: 'waiting_confirm',
            pendingConfirmations: [...existing, ...newConfs],
            messages: newMessages,
          });
        } else if (task.status !== get().agentStatus) {
          set({ agentStatus: task.status });
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
}));

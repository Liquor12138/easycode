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

  // ---- diff 内联合视图 ----
  diffDisplayContent: string;
  diffClassifications: { type: 'added' | 'removed' | 'unchanged' }[];

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
  diffDisplayContent: '',
  diffClassifications: [],

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
      error: null,
      plan: null,
      diffDisplayContent: '',
      diffClassifications: [],
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

  setActiveFile: (path: string | null) => set({ activeFile: path, diffDisplayContent: '', diffClassifications: [] }),

  // ============================================================
  // 计算行级 diff（LCS 算法）
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

        // 处理新的 step logs：为非文件修改工具生成消息
        const stepLogs = task.step_logs || [];
        if (stepLogs.length > processedStepCount) {
          for (let i = processedStepCount; i < stepLogs.length; i++) {
            const step = stepLogs[i];
            for (const tc of step.tool_calls) {
              // 文件修改工具通过确认流程显示，跳过重复
              if (tc.name === 'write_file' || tc.name === 'search_replace') continue;
              // 跳过计划管理工具
              if (['create_plan', 'update_step', 'finish_task'].includes(tc.name)) continue;

              let args: Record<string, unknown> = {};
              try { args = JSON.parse(tc.args); } catch { /* ignore */ }
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

        // 处理确认请求：打开文件并计算红绿 diff 装饰
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

            // 自动打开被修改的文件并计算红绿 diff
            const rawPath = String(conf.args.path || conf.args.file_path || '');
            if (rawPath) {
              // 路径规范化：统一使用 / 分隔符，解决 Windows \ 与 LLM / 不匹配问题
              const normalize = (p: string) => p.replace(/\\/g, '/');
              const filePath = normalize(rawPath);

              const state = get();
              // 尝试匹配 openFiles 中的文件（先精确匹配，再规范化匹配）
              let fileData = state.openFiles.get(filePath)
                || state.openFiles.get(rawPath)
                || (() => {
                    for (const [k, v] of state.openFiles) {
                      if (normalize(k) === filePath) return v;
                    }
                    return undefined;
                  })();
              // 找到匹配的实际 key（用于后续 set/get）
              const matchedKey = fileData
                ? (state.openFiles.has(filePath) ? filePath
                  : state.openFiles.has(rawPath) ? rawPath
                  : Array.from(state.openFiles.keys()).find(k => normalize(k) === filePath) || filePath)
                : filePath;

              // 语言推断
              const ext = filePath.split('.').pop() || '';
              const langMap: Record<string, string> = {
                py: 'python', js: 'javascript', ts: 'typescript',
                jsx: 'javascript', tsx: 'typescript', java: 'java',
                c: 'c', cpp: 'cpp', html: 'html', css: 'css',
                json: 'json', md: 'markdown', yaml: 'yaml', yml: 'yaml',
              };
              const language = langMap[ext] || 'plaintext';

              // 后端在确认前读取的原始文件内容（最可靠的数据源）
              const backendOriginal = conf.original_content
                || (conf.args._original_content as string)
                || '';

              let originalContent: string;
              let modifiedContent: string;
              let isNewFile = false;
              let updatedFilesMap: Map<string, { content: string; language: string }> | null = null;

              if (fileData) {
                // 文件已打开（存在于 openFiles 中）
                originalContent = fileData.content;
                if (conf.tool === 'search_replace') {
                  const oldText = (conf.args.old_text as string) || '';
                  const newText = (conf.args.new_text as string) || '';
                  modifiedContent = originalContent.replace(oldText, newText);
                } else {
                  modifiedContent = (conf.args.content as string) || '';
                }
              } else if (backendOriginal) {
                // 文件未打开，但后端提供了原始内容（最可靠）
                originalContent = backendOriginal;
                if (conf.tool === 'search_replace') {
                  const oldText = (conf.args.old_text as string) || '';
                  const newText = (conf.args.new_text as string) || '';
                  modifiedContent = originalContent.replace(oldText, newText);
                } else {
                  modifiedContent = (conf.args.content as string) || '';
                }
                // 将原始内容加入 openFiles，确保编辑器显示正确
                updatedFilesMap = new Map(get().openFiles);
                updatedFilesMap.set(filePath, { content: originalContent, language });
              } else {
                // 兜底：文件未打开且后端未提供原始内容，尝试多种策略加载
                isNewFile = true;
                originalContent = '';
                modifiedContent = (conf.args.content as string) || '';

                if (conf.tool === 'search_replace') {
                  // 策略 1：直接用 LLM 提供的路径请求 API
                  let loadedContent: string | null = null;
                  let loadedPath = filePath;
                  try {
                    const file = await api.fetchFileContent(rawPath);
                    loadedContent = file.content;
                  } catch {
                    // 策略 2：路径解析失败，在文件树中按文件名搜索正确路径
                    const fileName = rawPath.split('/').pop()?.split('\\').pop() || rawPath;
                    const treePath = findFileInTree(get().fileTree, fileName);
                    if (treePath) {
                      try {
                        const file = await api.fetchFileContent(treePath);
                        loadedContent = file.content;
                        loadedPath = treePath.replace(/\\/g, '/');
                      } catch {
                        // 仍然失败，放弃
                      }
                    }
                  }

                  if (loadedContent !== null) {
                    originalContent = loadedContent;
                    const oldText = (conf.args.old_text as string) || '';
                    const newText = (conf.args.new_text as string) || '';
                    modifiedContent = originalContent.replace(oldText, newText);
                    isNewFile = false;
                    updatedFilesMap = new Map(get().openFiles);
                    updatedFilesMap.set(loadedPath, { content: loadedContent, language });
                  }
                }

                if (isNewFile) {
                  updatedFilesMap = new Map(get().openFiles);
                  updatedFilesMap.set(filePath, { content: modifiedContent, language });
                }
              }

              // 构建内联 diff 视图（GitHub unified diff 风格）
              const diffResult = buildInlineDiff(originalContent, modifiedContent, conf.tool);
              // 一次性更新所有相关状态，避免 openFiles 与 diff 状态不一致
              set({
                diffDisplayContent: diffResult.displayContent,
                diffClassifications: diffResult.classifications,
                openFiles: updatedFilesMap || get().openFiles,
                activeFile: filePath,
              });
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
    const { taskId, pendingConfirmations, messages, openFiles } = get();
    if (!taskId) return;

    try {
      await api.respondConfirmation(taskId, confId, approved, reason);

      // 批准时：同步更新 openFiles 中的文件内容为修改后版本
      // 这样后续的 diff 计算才能基于正确的（已修改的）文件内容
      let updatedOpenFiles = openFiles;
      if (approved) {
        const conf = pendingConfirmations.find((c) => c.id === confId);
        if (conf) {
          const rawPath = String(conf.args.path || conf.args.file_path || '');
          const normalize = (p: string) => p.replace(/\\/g, '/');
          const filePath = normalize(rawPath);
          // 路径匹配：先精确匹配，再规范化匹配
          let fileData = openFiles.get(filePath)
            || openFiles.get(rawPath)
            || (() => {
                for (const [k, v] of openFiles) {
                  if (normalize(k) === filePath) return v;
                }
                return undefined;
              })();
          const matchedKey = fileData
            ? (openFiles.has(filePath) ? filePath
              : openFiles.has(rawPath) ? rawPath
              : Array.from(openFiles.keys()).find(k => normalize(k) === filePath) || filePath)
            : filePath;
          if (fileData) {
            let newContent: string;
            if (conf.tool === 'search_replace') {
              const oldText = (conf.args.old_text as string) || '';
              const newText = (conf.args.new_text as string) || '';
              newContent = fileData.content.replace(oldText, newText);
            } else if (conf.tool === 'write_file') {
              newContent = (conf.args.content as string) || '';
            } else {
              newContent = fileData.content;
            }
            updatedOpenFiles = new Map(openFiles);
            updatedOpenFiles.set(matchedKey, { ...fileData, content: newContent });
          }
        }
      }

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
        diffDisplayContent: '',
        diffClassifications: [],
        openFiles: updatedOpenFiles,
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

// ============================================================
// 文件树搜索：根据文件名在已加载的文件树中查找完整路径
// ============================================================

/** 在文件树中按文件名搜索，返回匹配的完整相对路径 */
function findFileInTree(nodes: import('../types').FileNode[], fileName: string): string | null {
  for (const node of nodes) {
    if (node.type === 'file' && node.name === fileName) {
      return node.path;
    }
    if (node.type === 'directory' && node.children) {
      const found = findFileInTree(node.children, fileName);
      if (found) return found;
    }
  }
  return null;
}

// ============================================================
// 内联 diff 构建（GitHub unified diff 风格）
// ============================================================

/** LCS 动态规划生成行级 diff 操作序列 */
function computeLCSOps(originalContent: string, modifiedContent: string): { op: 'keep' | 'remove' | 'add'; line: string }[] {
  const origLines = originalContent.split('\n');
  const modLines = modifiedContent.split('\n');
  const m = origLines.length;
  const n = modLines.length;

  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      dp[i][j] = origLines[i - 1] === modLines[j - 1]
        ? dp[i - 1][j - 1] + 1
        : Math.max(dp[i - 1][j], dp[i][j - 1]);
    }
  }

  // 回溯生成操作序列（逆序）
  const ops: { op: 'keep' | 'remove' | 'add'; line: string }[] = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && origLines[i - 1] === modLines[j - 1]) {
      ops.push({ op: 'keep', line: origLines[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      ops.push({ op: 'add', line: modLines[j - 1] });
      j--;
    } else {
      ops.push({ op: 'remove', line: origLines[i - 1] });
      i--;
    }
  }
  return ops.reverse();
}

/**
 * 构建内联 diff 视图：删除行和新增行交错排列在同一个内容中，
 * 类似 GitHub unified diff 的展示效果。
 */
function buildInlineDiff(
  originalContent: string,
  modifiedContent: string,
  tool: string,
): { displayContent: string; classifications: { type: 'added' | 'removed' | 'unchanged' }[] } {
  // 新文件：所有行都是新增
  if (!originalContent) {
    const lines = modifiedContent.split('\n');
    return {
      displayContent: modifiedContent,
      classifications: lines.map(() => ({ type: 'added' as const })),
    };
  }

  // 通用：使用 LCS 生成行级 diff，交错排列 removed 和 added
  const ops = computeLCSOps(originalContent, modifiedContent);
  const lines: string[] = [];
  const classifications: { type: 'added' | 'removed' | 'unchanged' }[] = [];

  for (const { op, line } of ops) {
    lines.push(line);
    classifications.push({
      type: op === 'keep' ? 'unchanged' : op === 'add' ? 'added' : 'removed',
    });
  }

  return { displayContent: lines.join('\n'), classifications };
}

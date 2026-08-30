import type {
  ApiStatus,
  FileContent,
  TaskState,
  ConfirmationInfo,
} from '../types';

const BASE = '/api';

// ============================================================
// 通用 fetch 封装
// ============================================================

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `请求失败: ${res.status} ${res.statusText}`);
  }
  return res.json();
}

// ============================================================
// 状态 & 工作目录
// ============================================================

export function fetchStatus(): Promise<ApiStatus> {
  return request<ApiStatus>('/status');
}

export function setWorkdir(path: string): Promise<{ working_dir: string; message: string }> {
  return request('/workdir', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });
}

// ============================================================
// 文件树 & 文件内容
// ============================================================

export function fetchFileTree(path = '.'): Promise<{ path: string; tree: import('../types').FileNode[] }> {
  return request(`/files?path=${encodeURIComponent(path)}`);
}

export function fetchFileContent(filePath: string): Promise<FileContent> {
  return request(`/file/${filePath}`);
}

// ============================================================
// 任务管理
// ============================================================

export function runTask(task: string, workingDir?: string): Promise<{ task_id: string; status: string }> {
  return request('/run', {
    method: 'POST',
    body: JSON.stringify({ task, working_dir: workingDir }),
  });
}

export function fetchTask(taskId: string): Promise<TaskState> {
  return request(`/task/${taskId}`);
}

export function fetchConfirmations(taskId: string): Promise<{ task_id: string; pending: ConfirmationInfo[] }> {
  return request(`/confirmations/${taskId}`);
}

export function respondConfirmation(
  taskId: string,
  confId: string,
  approved: boolean,
  reason = '',
): Promise<{ conf_id: string; approved: boolean; message: string }> {
  return request(`/confirm/${taskId}/${confId}`, {
    method: 'POST',
    body: JSON.stringify({ approved, reason }),
  });
}

// ============================================================
// 终端
// ============================================================

export function executeTerminalCommand(command: string): Promise<{ output: string; exit_code: number }> {
  return request('/terminal', {
    method: 'POST',
    body: JSON.stringify({ command }),
  });
}

// ============================================================
// 历史
// ============================================================

export function fetchHistory(): Promise<import('../types').TaskResult | { message: string }> {
  return request('/history');
}

// ============================================================
// 目录浏览 & 项目创建
// ============================================================

export interface BrowseEntry {
  name: string;
  path: string;
  type: 'directory' | 'drive';
}

export interface BrowseResult {
  current: string;
  parent?: string;
  entries: BrowseEntry[];
}

export function browseDirectories(path = ''): Promise<BrowseResult> {
  return request(`/browse?path=${encodeURIComponent(path)}`);
}

export function createProject(
  parentPath: string,
  projectName: string,
): Promise<{ path: string; name: string; message: string }> {
  return request('/create-project', {
    method: 'POST',
    body: JSON.stringify({ parent_path: parentPath, project_name: projectName }),
  });
}

export function pickNativeFolder(): Promise<{ selected: boolean; path: string }> {
  return request('/pick-folder');
}

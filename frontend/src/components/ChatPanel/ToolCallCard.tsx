import { useState } from 'react';
import type { ChatMessage } from '../../types';
import './ChatPanel.css';

interface ToolCallCardProps {
  message: ChatMessage;
}

const TOOL_DISPLAY: Record<string, { icon: string; colorClass: string }> = {
  read_file:        { icon: '📖', colorClass: 'tool-blue' },
  write_file:       { icon: '✏️', colorClass: 'tool-green' },
  execute_command:  { icon: '▶',  colorClass: 'tool-orange' },
  list_directory:   { icon: '📁', colorClass: 'tool-cyan' },
  search_replace:   { icon: '🔀', colorClass: 'tool-purple' },
  search_text:      { icon: '🔍', colorClass: 'tool-magenta' },
  get_diagnostics:  { icon: '⚡', colorClass: 'tool-yellow' },
  list_symbols:     { icon: '🔣', colorClass: 'tool-teal' },
  get_workdir:      { icon: '📂', colorClass: 'tool-gray' },
};

function getToolConfig(toolName?: string) {
  if (!toolName) return { icon: '⚙', colorClass: '' };
  return TOOL_DISPLAY[toolName] || { icon: '⚙', colorClass: '' };
}

export default function ToolCallCard({ message }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const toolCfg = getToolConfig(message.toolName);
  const isCommand = message.toolName === 'execute_command';
  const command = isCommand ? (message.toolArgs?.command as string) : '';

  const argsStr = message.toolArgs
    ? JSON.stringify(message.toolArgs, null, 2)
    : '';

  // 截断参数预览
  const argsPreview = argsStr.length > 120
    ? argsStr.slice(0, 120) + '...'
    : argsStr;

  return (
    <div className={`tool-call-card ${toolCfg.colorClass} ${message.rejected ? 'rejected' : ''}`}>
      <div className="tool-card-header" onClick={() => setExpanded(!expanded)}>
        <span className="tool-card-icon">{toolCfg.icon}</span>
        <span className="tool-card-name">{message.toolName}</span>
        {message.rejected && <span className="tool-card-rejected">已拒绝</span>}
        <span className="tool-card-toggle">{expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div className="tool-card-body">
          {/* execute_command: 展示运行的命令 */}
          {isCommand && command && (
            <div className="tool-card-section">
              <div className="tool-card-label">命令</div>
              <div className="tool-command-block">
                <span className="tool-command-prompt">$</span>
                <code className="tool-command-text">{command}</code>
              </div>
            </div>
          )}

          {/* 其他工具：显示参数 */}
          {!isCommand && argsStr && (
            <div className="tool-card-section">
              <div className="tool-card-label">参数</div>
              <pre className="tool-card-code">{argsPreview}</pre>
            </div>
          )}

          {message.toolResult && (
            <div className="tool-card-section">
              <div className="tool-card-label">{isCommand ? '输出' : '结果'}</div>
              <pre className="tool-card-result">{message.toolResult}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

import ReactMarkdown from 'react-markdown';
import type { ChatMessage } from '../../types';
import './ChatPanel.css';

interface MessageBubbleProps {
  message: ChatMessage;
}

// 工具显示配置：图标、颜色类名
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

export default function MessageBubble({ message }: MessageBubbleProps) {
  const { role, content, timestamp, rejected, toolName, toolArgs } = message;

  const timeStr = new Date(timestamp).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  });

  if (role === 'user') {
    return (
      <div className="message user-message">
        <div className="message-bubble user-bubble">
          <p>{content}</p>
        </div>
        <span className="message-time">{timeStr}</span>
      </div>
    );
  }

  if (role === 'result') {
    return (
      <div className="message result-message">
        <div className="message-bubble result-bubble">
          <div className="markdown-content">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
        </div>
        <span className="message-time">{timeStr}</span>
      </div>
    );
  }

  if (role === 'system') {
    return (
      <div className="message system-message">
        <div className="message-bubble system-bubble">
          <p>{content}</p>
        </div>
        <span className="message-time">{timeStr}</span>
      </div>
    );
  }

  if (role === 'tool_call') {
    const toolCfg = getToolConfig(toolName);
    const isCommand = toolName === 'execute_command';
    const command = isCommand ? (toolArgs?.command as string) : '';

    return (
      <div className={`message tool-message ${rejected ? 'rejected' : ''}`}>
        <div className={`message-bubble tool-bubble ${toolCfg.colorClass}`}>
          <div className="tool-call-header">
            <span className="tool-icon">{toolCfg.icon}</span>
            <span className="tool-name">{toolName || 'tool'}</span>
            {rejected && <span className="tool-rejected-badge">已拒绝</span>}
          </div>

          {/* execute_command: 展示运行的命令 */}
          {isCommand && command && (
            <div className="tool-command-block">
              <span className="tool-command-prompt">$</span>
              <code className="tool-command-text">{command}</code>
            </div>
          )}

          {/* 非命令工具：显示内容预览 */}
          {!isCommand && (
            <div className="tool-call-preview">
              <ReactMarkdown>{content}</ReactMarkdown>
            </div>
          )}

          {/* 执行结果 */}
          {message.toolResult && (
            <div className={`tool-call-result ${isCommand ? 'tool-command-output' : ''}`}>
              {isCommand && <div className="tool-card-label">输出</div>}
              <pre className="tool-result-pre">{message.toolResult}</pre>
            </div>
          )}
        </div>
        <span className="message-time">{timeStr}</span>
      </div>
    );
  }

  // assistant
  return (
    <div className="message assistant-message">
      <div className="message-bubble assistant-bubble">
        <div className="markdown-content">
          <ReactMarkdown>{content}</ReactMarkdown>
        </div>
      </div>
      <span className="message-time">{timeStr}</span>
    </div>
  );
}

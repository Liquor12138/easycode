import { useState } from 'react';
import type { ChatMessage } from '../../types';
import './ChatPanel.css';

interface ToolCallCardProps {
  message: ChatMessage;
}

export default function ToolCallCard({ message }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);

  const argsStr = message.toolArgs
    ? JSON.stringify(message.toolArgs, null, 2)
    : '';

  // 截断参数预览
  const argsPreview = argsStr.length > 120
    ? argsStr.slice(0, 120) + '...'
    : argsStr;

  return (
    <div className={`tool-call-card ${message.rejected ? 'rejected' : ''}`}>
      <div className="tool-card-header" onClick={() => setExpanded(!expanded)}>
        <span className="tool-card-icon">&#9881;</span>
        <span className="tool-card-name">{message.toolName}</span>
        {message.rejected && <span className="tool-card-rejected">已拒绝</span>}
        <span className="tool-card-toggle">{expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div className="tool-card-body">
          {argsStr && (
            <div className="tool-card-section">
              <div className="tool-card-label">参数</div>
              <pre className="tool-card-code">{argsPreview}</pre>
            </div>
          )}
          {message.toolResult && (
            <div className="tool-card-section">
              <div className="tool-card-label">结果</div>
              <pre className="tool-card-result">{message.toolResult}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

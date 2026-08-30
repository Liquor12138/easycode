import ReactMarkdown from 'react-markdown';
import type { ChatMessage } from '../../types';
import './ChatPanel.css';

interface MessageBubbleProps {
  message: ChatMessage;
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const { role, content, timestamp, rejected } = message;

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
    return (
      <div className={`message tool-message ${rejected ? 'rejected' : ''}`}>
        <div className="message-bubble tool-bubble">
          <div className="tool-call-header">
            <span className="tool-icon">&#9881;</span>
            <span className="tool-name">{message.toolName || 'tool'}</span>
            {rejected && <span className="tool-rejected-badge">已拒绝</span>}
          </div>
          <div className="tool-call-preview">
            <ReactMarkdown>{content}</ReactMarkdown>
          </div>
          {message.toolResult && (
            <div className="tool-call-result">{message.toolResult}</div>
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

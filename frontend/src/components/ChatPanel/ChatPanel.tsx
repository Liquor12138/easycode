import { useState, useRef, useEffect } from 'react';
import { useAgentStore } from '../../store/useAgentStore';
import MessageBubble from './MessageBubble';
import './ChatPanel.css';

export default function ChatPanel() {
  const { messages, agentStatus, sendMessage, pendingConfirmations } = useAgentStore();
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || agentStatus === 'running' || agentStatus === 'pending') return;
    setInput('');
    sendMessage(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const isRunning = agentStatus === 'running' || agentStatus === 'pending';
  const hasPendingConf = pendingConfirmations.length > 0;
  const canSend = !isRunning;

  return (
    <div className="chat-panel">
      {/* 状态栏 */}
      <div className="chat-status-bar">
        <span className="chat-status-label">对话</span>
        {isRunning && (
          <span className="chat-running-indicator">
            <span className="running-dot" />
            Agent 运行中
          </span>
        )}
        {hasPendingConf && (
          <span className="chat-confirm-indicator">
            {pendingConfirmations.length} 个待确认
          </span>
        )}
      </div>

      {/* 消息列表 */}
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <p>发送消息开始与 Agent 对话</p>
            <p className="chat-empty-hint">
              Agent 将自动制定计划并逐步执行任务
            </p>
          </div>
        )}
        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* 输入区 */}
      <div className="chat-input-area">
        <textarea
          ref={inputRef}
          className="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={isRunning ? 'Agent 正在执行任务...' : '输入任务描述... (Enter 发送, Shift+Enter 换行)'}
          disabled={!canSend}
          rows={3}
        />
        <button
          className="chat-send-btn"
          onClick={handleSend}
          disabled={!input.trim() || !canSend}
        >
          发送
        </button>
      </div>
    </div>
  );
}

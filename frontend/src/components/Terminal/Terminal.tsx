import { useState, useRef, useEffect } from 'react';
import { useAgentStore } from '../../store/useAgentStore';
import './Terminal.css';

export default function Terminal() {
  const { terminalOpen, toggleTerminal, terminalEntries, executeCommand } = useAgentStore();
  const [input, setInput] = useState('');
  const [historyIndex, setHistoryIndex] = useState(-1);
  const outputRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // 自动滚动到底部
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [terminalEntries]);

  // 终端打开时聚焦输入
  useEffect(() => {
    if (terminalOpen) {
      inputRef.current?.focus();
    }
  }, [terminalOpen]);

  const handleSubmit = () => {
    const cmd = input.trim();
    if (!cmd) return;
    executeCommand(cmd);
    setInput('');
    setHistoryIndex(-1);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSubmit();
      return;
    }
    // 命令历史
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      const entries = terminalEntries;
      if (entries.length === 0) return;
      const newIndex = Math.min(historyIndex + 1, entries.length - 1);
      setHistoryIndex(newIndex);
      setInput(entries[entries.length - 1 - newIndex].command);
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (historyIndex <= 0) {
        setHistoryIndex(-1);
        setInput('');
        return;
      }
      const newIndex = historyIndex - 1;
      setHistoryIndex(newIndex);
      setInput(terminalEntries[terminalEntries.length - 1 - newIndex].command);
    }
  };

  return (
    <div className={`terminal-panel ${terminalOpen ? 'open' : ''}`}>
      {/* 标题栏 */}
      <div className="terminal-header" onClick={toggleTerminal}>
        <span className="terminal-title">终端</span>
        <span className="terminal-toggle">{terminalOpen ? '▼' : '▲'}</span>
      </div>

      {/* 终端内容 */}
      {terminalOpen && (
        <div className="terminal-body">
          <div className="terminal-output" ref={outputRef}>
            {terminalEntries.length === 0 && (
              <div className="terminal-empty">输入命令执行...</div>
            )}
            {terminalEntries.map((entry) => (
              <div key={entry.id} className="terminal-entry">
                <div className="terminal-cmd-line">
                  <span className="terminal-prompt">$</span>
                  <span className="terminal-cmd">{entry.command}</span>
                </div>
                {entry.output && (
                  <pre className={`terminal-output-text ${entry.exitCode !== 0 ? 'error' : ''}`}>
                    {entry.output}
                  </pre>
                )}
              </div>
            ))}
          </div>
          <div className="terminal-input-line">
            <span className="terminal-prompt">$</span>
            <input
              ref={inputRef}
              className="terminal-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入命令..."
              spellCheck={false}
            />
          </div>
        </div>
      )}
    </div>
  );
}

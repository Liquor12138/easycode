import { useEffect, useState } from 'react';
import { useAgentStore } from '../../store/useAgentStore';
import './Header.css';

export default function Header() {
  const { workingDir, agentStatus, error, clearError, initStatus } = useAgentStore();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    initStatus().then(() => setConnected(true)).catch(() => setConnected(false));
  }, []);

  const statusLabel: Record<string, string> = {
    idle: '空闲',
    pending: '启动中...',
    running: '运行中',
    waiting_confirm: '等待确认',
    completed: '已完成',
    failed: '失败',
  };

  const statusClass = `status-badge status-${agentStatus}`;

  return (
    <header className="app-header">
      <div className="header-left">
        <span className="header-logo">Coding Agent</span>
        <span className={`connection-dot ${connected ? 'connected' : 'disconnected'}`} />
      </div>

      <div className="header-center">
        {error && (
          <div className="header-error" onClick={clearError}>
            {error}
            <span className="error-dismiss">x</span>
          </div>
        )}
      </div>

      <div className="header-right">
        <span className={statusClass}>{statusLabel[agentStatus] || '空闲'}</span>
        <span className="header-workdir" title={workingDir}>
          {workingDir ? workingDir.split(/[/\\]/).pop() : '未连接'}
        </span>
      </div>
    </header>
  );
}

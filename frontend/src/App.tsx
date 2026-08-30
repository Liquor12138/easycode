import { useState } from 'react';
import Header from './components/common/Header';
import FileExplorer from './components/FileExplorer/FileExplorer';
import CodeViewer from './components/CodeViewer/CodeViewer';
import DiffViewer from './components/CodeViewer/DiffViewer';
import ChatPanel from './components/ChatPanel/ChatPanel';
import Terminal from './components/Terminal/Terminal';
import { useAgentStore } from './store/useAgentStore';
import './App.css';

export default function App() {
  const [explorerOpen, setExplorerOpen] = useState(true);
  const { pendingConfirmations, openFiles, activeFile } = useAgentStore();

  // 获取当前活跃文件的内容（用于 diff 对比）
  const activeFileData = activeFile ? openFiles.get(activeFile) : null;

  return (
    <div className="app-container">
      <Header />

      <div className="app-body">
        {/* 左侧：文件浏览器 */}
        {explorerOpen && (
          <div className="panel-left">
            <FileExplorer />
          </div>
        )}

        {/* 折叠按钮 */}
        <div
          className="panel-resizer panel-resizer-left"
          onClick={() => setExplorerOpen(!explorerOpen)}
          title={explorerOpen ? '收起文件浏览器' : '展开文件浏览器'}
        >
          <span className="resizer-icon">{explorerOpen ? '◀' : '▶'}</span>
        </div>

        {/* 中间：代码查看器 + Diff + 终端 */}
        <div className="panel-center">
          {/* 如果有待确认的修改，显示 Diff 视图 */}
          {pendingConfirmations.length > 0 && activeFileData && (
            <DiffViewer
              confirmation={pendingConfirmations[0]}
              originalContent={activeFileData.content}
              language={activeFileData.language}
            />
          )}

          {/* 代码查看器 */}
          <div className="center-editor">
            <CodeViewer />
          </div>

          {/* 底部终端 */}
          <Terminal />
        </div>

        {/* 右侧：对话面板 */}
        <div className="panel-right">
          <ChatPanel />
        </div>
      </div>
    </div>
  );
}

import { useState } from 'react';
import Header from './components/common/Header';
import FileExplorer from './components/FileExplorer/FileExplorer';
import CodeViewer from './components/CodeViewer/CodeViewer';
import DiffViewer from './components/CodeViewer/DiffViewer';
import ChatPanel from './components/ChatPanel/ChatPanel';
import Terminal from './components/Terminal/Terminal';
import WelcomePage from './components/WelcomePage/WelcomePage';
import { useAgentStore } from './store/useAgentStore';
import './App.css';

export default function App() {
  const [explorerOpen, setExplorerOpen] = useState(true);
  const { projectSelected, pendingConfirmations, openFiles, activeFile } = useAgentStore();

  const activeFileData = activeFile ? openFiles.get(activeFile) : null;

  // 未选择项目时显示全屏欢迎页
  if (!projectSelected) {
    return <WelcomePage />;
  }

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
          {pendingConfirmations.length > 0 && activeFileData && (
            <DiffViewer
              confirmation={pendingConfirmations[0]}
              originalContent={activeFileData.content}
              language={activeFileData.language}
            />
          )}

          <div className="center-editor">
            <CodeViewer />
          </div>

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

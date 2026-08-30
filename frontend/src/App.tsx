import { useState, useMemo } from 'react';
import Header from './components/common/Header';
import FileExplorer from './components/FileExplorer/FileExplorer';
import CodeViewer from './components/CodeViewer/CodeViewer';
import DiffViewer from './components/CodeViewer/DiffViewer';
import ChatPanel from './components/ChatPanel/ChatPanel';
import Terminal from './components/Terminal/Terminal';
import WelcomePage from './components/WelcomePage/WelcomePage';
import { useAgentStore } from './store/useAgentStore';
import './App.css';

// 根据文件扩展名推断 Monaco 语言
function inferLanguage(filePath: string): string {
  const ext = filePath.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, string> = {
    ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
    py: 'python', html: 'html', css: 'css', json: 'json', md: 'markdown',
    yaml: 'yaml', yml: 'yaml', xml: 'xml', sql: 'sql', sh: 'shell',
    bash: 'shell', java: 'java', c: 'c', cpp: 'cpp', cs: 'csharp',
    go: 'go', rs: 'rust', rb: 'ruby', php: 'php', swift: 'swift',
    kt: 'kotlin', scala: 'scala', r: 'r', toml: 'toml', ini: 'ini',
    dockerfile: 'dockerfile', txt: 'plaintext',
  };
  return map[ext] || 'plaintext';
}

export default function App() {
  const [explorerOpen, setExplorerOpen] = useState(true);
  const { projectSelected, pendingConfirmations, openFiles, activeFile } = useAgentStore();

  const activeFileData = activeFile ? openFiles.get(activeFile) : null;

  // 当前待确认的文件路径与内容（兼容文件不存在的情况）
  const currentConf = pendingConfirmations.length > 0 ? pendingConfirmations[0] : null;
  const confFilePath = currentConf
    ? String(currentConf.args.path || currentConf.args.file_path || '')
    : '';
  const diffData = useMemo(() => {
    if (!currentConf) return null;
    // 如果当前打开的文件正好是待确认文件，使用其内容
    if (activeFileData && activeFile === confFilePath) {
      return { originalContent: activeFileData.content, language: activeFileData.language };
    }
    // 文件不存在或未打开：原始内容为空，从扩展名推断语言
    return { originalContent: '', language: inferLanguage(confFilePath) };
  }, [currentConf, activeFileData, activeFile, confFilePath]);

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
          {diffData && currentConf && (
            <DiffViewer
              confirmation={currentConf}
              originalContent={diffData.originalContent}
              language={diffData.language}
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

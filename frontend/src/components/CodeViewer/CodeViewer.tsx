import Editor from '@monaco-editor/react';
import { useAgentStore } from '../../store/useAgentStore';
import './CodeViewer.css';

export default function CodeViewer() {
  const { openFiles, activeFile, closeFile, setActiveFile } = useAgentStore();

  const activeData = activeFile ? openFiles.get(activeFile) : null;

  if (openFiles.size === 0) {
    return (
      <div className="code-viewer empty">
        <div className="code-viewer-placeholder">
          <p>选择一个文件以查看代码</p>
          <p className="hint">从左侧文件浏览器中点击文件打开</p>
        </div>
      </div>
    );
  }

  return (
    <div className="code-viewer">
      {/* Tab bar */}
      <div className="code-tabs">
        {Array.from(openFiles.entries()).map(([path]) => (
          <div
            key={path}
            className={`code-tab ${path === activeFile ? 'active' : ''}`}
            onClick={() => setActiveFile(path)}
          >
            <span className="code-tab-name">{path.split(/[/\\]/).pop()}</span>
            <span
              className="code-tab-close"
              onClick={(e) => {
                e.stopPropagation();
                closeFile(path);
              }}
            >
              &times;
            </span>
          </div>
        ))}
      </div>

      {/* Editor */}
      <div className="code-editor-wrapper">
        {activeData ? (
          <Editor
            height="100%"
            language={activeData.language}
            value={activeData.content}
            theme="vs-dark"
            options={{
              readOnly: true,
              minimap: { enabled: false },
              fontSize: 13,
              lineNumbers: 'on',
              scrollBeyondLastLine: false,
              wordWrap: 'on',
              automaticLayout: true,
              padding: { top: 8 },
            }}
          />
        ) : (
          <div className="code-viewer-placeholder">
            <p>无法加载文件内容</p>
          </div>
        )}
      </div>
    </div>
  );
}

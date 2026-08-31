import { useEffect, useRef } from 'react';
import Editor, { type Monaco } from '@monaco-editor/react';
import type { editor } from 'monaco-editor';
import { useAgentStore } from '../../store/useAgentStore';
import './CodeViewer.css';

export default function CodeViewer() {
  const { openFiles, activeFile, closeFile, setActiveFile, diffDecorations, pendingConfirmations, confirmChange, taskId } = useAgentStore();
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const decorationsRef = useRef<string[]>([]);

  const activeData = activeFile ? openFiles.get(activeFile) : null;

  // 当前待确认修改
  const currentConf = pendingConfirmations.length > 0 ? pendingConfirmations[0] : null;

  // 当 diff 装饰或活动文件变化时，更新 Monaco decorations
  useEffect(() => {
    const ed = editorRef.current;
    const monaco = monacoRef.current;
    if (!ed || !monaco) return;

    if (decorationsRef.current.length > 0) {
      decorationsRef.current = ed.deltaDecorations(decorationsRef.current, []);
    }

    if (diffDecorations.length > 0 && activeFile) {
      const newDecorations: editor.IModelDeltaDecoration[] = diffDecorations.map((d) => ({
        range: new monaco.Range(d.line, 1, d.line, 1),
        options: {
          isWholeLine: true,
          className: d.type === 'added' ? 'diff-line-added' : 'diff-line-removed',
          linesDecorationsClassName: d.type === 'added' ? 'diff-line-added-glyph' : 'diff-line-removed-glyph',
        },
      }));
      decorationsRef.current = ed.deltaDecorations([], newDecorations);
      // 自动滚动到第一个变化行
      if (diffDecorations.length > 0) {
        ed.revealLineInCenter(diffDecorations[0].line);
      }
    }
  }, [diffDecorations, activeFile]);

  // 切换文件时清除 diff 装饰
  useEffect(() => {
    const ed = editorRef.current;
    if (!ed) return;
    if (decorationsRef.current.length > 0) {
      decorationsRef.current = ed.deltaDecorations(decorationsRef.current, []);
    }
  }, [activeFile]);

  const handleAccept = () => {
    if (taskId && currentConf) {
      confirmChange(currentConf.id, true);
    }
  };

  const handleReject = () => {
    if (taskId && currentConf) {
      confirmChange(currentConf.id, false, '用户拒绝此修改');
    }
  };

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

      {/* 确认栏：显示待确认修改信息 */}
      {currentConf && (
        <div className="diff-header">
          <span className="diff-title">
            Agent 请求修改: <strong>{String(currentConf.args.path || currentConf.args.file_path || '')}</strong>
          </span>
          <span className="diff-tool">{currentConf.tool}</span>
        </div>
      )}

      {/* Editor */}
      <div className="code-editor-wrapper">
        {activeData ? (
          <Editor
            height="100%"
            language={activeData.language}
            value={activeData.content}
            theme="vs-dark"
            onMount={(editor, monaco) => {
              editorRef.current = editor;
              monacoRef.current = monaco;
            }}
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

      {/* 确认按钮 */}
      {currentConf && (
        <div className="diff-actions">
          <button className="btn-accept" onClick={handleAccept}>
            接受修改
          </button>
          <button className="btn-reject" onClick={handleReject}>
            拒绝
          </button>
        </div>
      )}
    </div>
  );
}

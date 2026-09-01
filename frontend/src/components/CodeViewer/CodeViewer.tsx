import { useEffect, useRef } from 'react';
import Editor, { type Monaco } from '@monaco-editor/react';
import type { editor } from 'monaco-editor';
import { useAgentStore } from '../../store/useAgentStore';
import './CodeViewer.css';

export default function CodeViewer() {
  const {
    openFiles, activeFile, closeFile, setActiveFile,
    diffDisplayContent, diffClassifications,
    pendingConfirmations, confirmChange, taskId,
  } = useAgentStore();
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const decorationsRef = useRef<string[]>([]);

  const activeData = activeFile ? openFiles.get(activeFile) : null;

  const currentConf = pendingConfirmations.length > 0 ? pendingConfirmations[0] : null;
  const hasDiff = diffClassifications.length > 0 && diffDisplayContent !== '';
  const displayValue = hasDiff ? diffDisplayContent : (activeData?.content ?? '');
  const language = activeData?.language || 'plaintext';

  // 当 diff 数据或活动文件变化时，更新 Monaco decorations
  useEffect(() => {
    const ed = editorRef.current;
    const monaco = monacoRef.current;
    if (!ed || !monaco) return;

    if (decorationsRef.current.length > 0) {
      decorationsRef.current = ed.deltaDecorations(decorationsRef.current, []);
    }

    if (!hasDiff) return;

    const newDecorations: editor.IModelDeltaDecoration[] = [];
    for (let i = 0; i < diffClassifications.length; i++) {
      const cls = diffClassifications[i];
      const lineNum = i + 1;
      if (cls.type === 'added') {
        newDecorations.push({
          range: new monaco.Range(lineNum, 1, lineNum, 1),
          options: {
            isWholeLine: true,
            className: 'diff-line-added',
            linesDecorationsClassName: 'diff-line-added-glyph',
          },
        });
      } else if (cls.type === 'removed') {
        newDecorations.push({
          range: new monaco.Range(lineNum, 1, lineNum, 1),
          options: {
            isWholeLine: true,
            className: 'diff-line-removed',
            linesDecorationsClassName: 'diff-line-removed-glyph',
            inlineClassName: 'diff-line-removed-text',
          },
        });
      }
    }
    decorationsRef.current = ed.deltaDecorations([], newDecorations);

    const firstChangeIdx = diffClassifications.findIndex(c => c.type !== 'unchanged');
    if (firstChangeIdx >= 0) ed.revealLineInCenter(firstChangeIdx + 1);
  }, [diffDisplayContent, diffClassifications, hasDiff]);

  // 切换文件时清除装饰
  useEffect(() => {
    const ed = editorRef.current;
    if (!ed) return;
    if (decorationsRef.current.length > 0) {
      decorationsRef.current = ed.deltaDecorations(decorationsRef.current, []);
    }
  }, [activeFile]);

  const handleAccept = () => {
    if (taskId && currentConf) confirmChange(currentConf.id, true);
  };
  const handleReject = () => {
    if (taskId && currentConf) confirmChange(currentConf.id, false, '用户拒绝此修改');
  };

  const addedCount = diffClassifications.filter(c => c.type === 'added').length;
  const removedCount = diffClassifications.filter(c => c.type === 'removed').length;

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

      {/* 确认信息栏 */}
      {currentConf && (
        <div className="diff-confirm-bar">
          <div className="diff-confirm-header">
            <span className="diff-confirm-icon">✎</span>
            <span className="diff-confirm-title">
              Agent 请求修改: <strong>{String(currentConf.args.path || currentConf.args.file_path || '')}</strong>
            </span>
            <span className="diff-confirm-tool">{currentConf.tool}</span>
          </div>
          {(addedCount > 0 || removedCount > 0) && (
            <div className="diff-confirm-stats">
              {addedCount > 0 && <span className="diff-stat-added">+{addedCount} 行新增</span>}
              {removedCount > 0 && <span className="diff-stat-removed">-{removedCount} 行删除</span>}
            </div>
          )}
        </div>
      )}

      {/* Editor */}
      <div className="code-editor-wrapper">
        {activeData ? (
          <Editor
            key={hasDiff ? 'diff-view' : 'normal-view'}
            height="100%"
            language={language}
            value={displayValue}
            theme="vs-dark"
            onMount={(editor, monaco) => {
              editorRef.current = editor;
              monacoRef.current = monaco;
              // 编辑器重新挂载后立即应用 diff 装饰（解决 useEffect 时序问题）
              if (hasDiff && diffClassifications.length > 0) {
                const decs: editor.IModelDeltaDecoration[] = [];
                for (let i = 0; i < diffClassifications.length; i++) {
                  const cls = diffClassifications[i];
                  const lineNum = i + 1;
                  if (cls.type === 'added') {
                    decs.push({
                      range: new monaco.Range(lineNum, 1, lineNum, 1),
                      options: { isWholeLine: true, className: 'diff-line-added', linesDecorationsClassName: 'diff-line-added-glyph' },
                    });
                  } else if (cls.type === 'removed') {
                    decs.push({
                      range: new monaco.Range(lineNum, 1, lineNum, 1),
                      options: { isWholeLine: true, className: 'diff-line-removed', linesDecorationsClassName: 'diff-line-removed-glyph', inlineClassName: 'diff-line-removed-text' },
                    });
                  }
                }
                decorationsRef.current = editor.deltaDecorations([], decs);
                const firstChangeIdx = diffClassifications.findIndex(c => c.type !== 'unchanged');
                if (firstChangeIdx >= 0) editor.revealLineInCenter(firstChangeIdx + 1);
              }
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
          <button className="btn-reject" onClick={handleReject}>
            ✕ 拒绝
          </button>
          <button className="btn-accept" onClick={handleAccept}>
            ✓ 接受修改
          </button>
        </div>
      )}
    </div>
  );
}

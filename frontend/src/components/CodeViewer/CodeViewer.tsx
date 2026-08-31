import { useEffect, useRef } from 'react';
import Editor, { type Monaco } from '@monaco-editor/react';
import type { editor } from 'monaco-editor';
import { useAgentStore } from '../../store/useAgentStore';
import './CodeViewer.css';

export default function CodeViewer() {
  const { openFiles, activeFile, closeFile, setActiveFile, highlightLines } = useAgentStore();
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<Monaco | null>(null);
  const decorationsRef = useRef<string[]>([]);

  const activeData = activeFile ? openFiles.get(activeFile) : null;

  // 当高亮行或活动文件变化时，更新 Monaco decorations
  useEffect(() => {
    const ed = editorRef.current;
    const monaco = monacoRef.current;
    if (!ed || !monaco) return;

    // 清除旧的 decorations
    if (decorationsRef.current.length > 0) {
      decorationsRef.current = ed.deltaDecorations(decorationsRef.current, []);
    }

    if (highlightLines && activeFile) {
      const newDecorations: editor.IModelDeltaDecoration[] = [];
      for (let line = highlightLines.start; line <= highlightLines.end; line++) {
        newDecorations.push({
          range: new monaco.Range(line, 1, line, 1),
          options: {
            isWholeLine: true,
            className: 'highlighted-line',
            linesDecorationsClassName: 'highlighted-line-glyph',
          },
        });
      }
      decorationsRef.current = ed.deltaDecorations([], newDecorations);
      // 自动滚动到第一个高亮行
      ed.revealLineInCenter(highlightLines.start);
    }
  }, [highlightLines, activeFile]);

  // 切换文件时清除高亮
  useEffect(() => {
    const ed = editorRef.current;
    if (!ed) return;
    if (decorationsRef.current.length > 0) {
      decorationsRef.current = ed.deltaDecorations(decorationsRef.current, []);
    }
  }, [activeFile]);

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
    </div>
  );
}

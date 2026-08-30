import { useMemo } from 'react';
import { DiffEditor } from '@monaco-editor/react';
import { useAgentStore } from '../../store/useAgentStore';
import type { ConfirmationInfo } from '../../types';
import './CodeViewer.css';

interface DiffViewerProps {
  confirmation: ConfirmationInfo;
  originalContent: string;
  language: string;
}

export default function DiffViewer({ confirmation, originalContent, language }: DiffViewerProps) {
  const { confirmChange, taskId } = useAgentStore();

  // 计算修改后的内容
  const modifiedContent = useMemo(() => {
    const args = confirmation.args;
    if (confirmation.tool === 'write_file') {
      return (args.content as string) || '';
    }
    if (confirmation.tool === 'search_replace') {
      const oldText = (args.old_text as string) || '';
      const newText = (args.new_text as string) || '';
      return originalContent.replace(oldText, newText);
    }
    return '';
  }, [confirmation, originalContent]);

  const filePath = String(confirmation.args.path || confirmation.args.file_path || 'unknown');

  const handleAccept = () => {
    if (taskId) confirmChange(confirmation.id, true);
  };

  const handleReject = () => {
    if (taskId) confirmChange(confirmation.id, false, '用户拒绝此修改');
  };

  return (
    <div className="diff-viewer">
      <div className="diff-header">
        <span className="diff-title">
          Agent 请求修改: <strong>{filePath}</strong>
        </span>
        <span className="diff-tool">{confirmation.tool}</span>
      </div>

      <div className="diff-preview">
        <div className="diff-preview-text">{confirmation.preview}</div>
      </div>

      <div className="diff-editor-wrapper">
        <DiffEditor
          height="300px"
          language={language}
          original={originalContent}
          modified={modifiedContent}
          theme="vs-dark"
          options={{
            readOnly: true,
            renderSideBySide: true,
            minimap: { enabled: false },
            fontSize: 13,
            lineNumbers: 'on',
            scrollBeyondLastLine: false,
            automaticLayout: true,
          }}
        />
      </div>

      <div className="diff-actions">
        <button className="btn-accept" onClick={handleAccept}>
          接受修改
        </button>
        <button className="btn-reject" onClick={handleReject}>
          拒绝
        </button>
      </div>
    </div>
  );
}

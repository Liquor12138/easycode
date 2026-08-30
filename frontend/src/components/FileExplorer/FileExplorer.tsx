import { useState } from 'react';
import { FiChevronRight, FiChevronDown, FiFile, FiFolder } from 'react-icons/fi';
import { useAgentStore } from '../../store/useAgentStore';
import type { FileNode } from '../../types';
import './FileExplorer.css';

// ============================================================
// 文件树节点
// ============================================================

function TreeNode({ node, depth }: { node: FileNode; depth: number }) {
  const [expanded, setExpanded] = useState(depth < 1);
  const { openFile, activeFile } = useAgentStore();

  const isDir = node.type === 'directory';
  const isActive = activeFile === node.path;

  const handleClick = () => {
    if (isDir) {
      setExpanded(!expanded);
    } else {
      openFile(node.path);
    }
  };

  return (
    <div className="tree-node">
      <div
        className={`tree-node-label ${isActive ? 'active' : ''}`}
        style={{ paddingLeft: depth * 16 + 8 }}
        onClick={handleClick}
      >
        {isDir ? (
          expanded ? <FiChevronDown size={14} /> : <FiChevronRight size={14} />
        ) : (
          <span className="tree-indent" />
        )}
        {isDir ? (
          <FiFolder size={14} className={`icon-folder ${expanded ? 'expanded' : ''}`} />
        ) : (
          <FiFile size={14} className="icon-file" />
        )}
        <span className="tree-node-name">{node.name}</span>
      </div>
      {isDir && expanded && node.children && (
        <div className="tree-node-children">
          {node.children.map((child) => (
            <TreeNode key={child.path} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

// ============================================================
// 文件浏览器主组件
// ============================================================

export default function FileExplorer() {
  const { fileTree, workingDir, loadFileTree, loadingFiles, resetToWelcome } = useAgentStore();

  return (
    <div className="file-explorer">
      <div className="explorer-header">
        <span className="explorer-title">文件浏览器</span>
        <div className="explorer-header-actions">
          <button className="explorer-refresh" onClick={loadFileTree} title="刷新">
            &#x21bb;
          </button>
          <button className="explorer-reselect" onClick={resetToWelcome} title="重新选择项目">
            &#x21B6;
          </button>
        </div>
      </div>

      <div className="explorer-workdir" title={workingDir}>
        {workingDir || '未连接'}
      </div>

      <div className="explorer-tree">
        {loadingFiles ? (
          <div className="explorer-loading">加载中...</div>
        ) : fileTree.length === 0 ? (
          <div className="explorer-empty">暂无文件</div>
        ) : (
          fileTree.map((node) => <TreeNode key={node.path} node={node} depth={0} />)
        )}
      </div>
    </div>
  );
}

import { useState } from 'react';
import { FiFolder } from 'react-icons/fi';
import { useAgentStore } from '../../store/useAgentStore';
import { pickNativeFolder } from '../../api/client';
import './WelcomePage.css';

export default function WelcomePage() {
  const { openProject, createAndOpenProject, error } = useAgentStore();
  const [loading, setLoading] = useState(false);
  const [showNameInput, setShowNameInput] = useState(false);
  const [pickedParentPath, setPickedParentPath] = useState('');
  const [projectName, setProjectName] = useState('');
  const [nameError, setNameError] = useState('');

  // ---- 打开项目：调用原生对话框 ----
  const handleOpenProject = async () => {
    setLoading(true);
    try {
      const res = await pickNativeFolder();
      if (res.selected && res.path) {
        await openProject(res.path);
      }
    } catch {
      // error 已由 store 处理
    }
    setLoading(false);
  };

  // ---- 创建项目：先选路径，再输入名称 ----
  const handleCreateProject = async () => {
    setLoading(true);
    try {
      const res = await pickNativeFolder();
      if (res.selected && res.path) {
        setPickedParentPath(res.path);
        setShowNameInput(true);
        setNameError('');
      }
    } catch {
      // error 已由 store 处理
    }
    setLoading(false);
  };

  // ---- 确认创建 ----
  const handleConfirmCreate = async () => {
    const name = projectName.trim();
    if (!name) {
      setNameError('请输入项目名称');
      return;
    }
    setLoading(true);
    setNameError('');
    try {
      await createAndOpenProject(pickedParentPath, name);
    } catch {
      // error 已由 store 处理
    }
    setLoading(false);
    setShowNameInput(false);
    setProjectName('');
  };

  const handleCancelCreate = () => {
    setShowNameInput(false);
    setPickedParentPath('');
    setProjectName('');
    setNameError('');
  };

  return (
    <div className="welcome-page">
      <div className="welcome-content">
        {/* 品牌 */}
        <div className="welcome-brand">
          <h1 className="welcome-logo">EasyCode</h1>
          <p className="welcome-tagline">你的vibe-coding搭子</p>
        </div>

        {/* 创建项目：输入名称弹窗 */}
        {showNameInput && (
          <div className="welcome-name-dialog">
            <p className="name-dialog-title">创建新项目</p>
            <p className="name-dialog-path">位置: {pickedParentPath}</p>
            <div className="name-dialog-input-row">
              <label>项目名称</label>
              <input
                type="text"
                className="name-dialog-input"
                value={projectName}
                onChange={(e) => { setProjectName(e.target.value); setNameError(''); }}
                onKeyDown={(e) => e.key === 'Enter' && handleConfirmCreate()}
                placeholder="输入项目名称..."
                autoFocus
                spellCheck={false}
              />
            </div>
            {nameError && <p className="name-dialog-error">{nameError}</p>}
            <div className="name-dialog-actions">
              <button className="name-dialog-btn name-dialog-btn-cancel" onClick={handleCancelCreate}>
                取消
              </button>
              <button className="name-dialog-btn name-dialog-btn-confirm" onClick={handleConfirmCreate}>
                创建
              </button>
            </div>
          </div>
        )}

        {/* 两个主按钮 */}
        {!showNameInput && (
          <div className="welcome-buttons">
            <button
              className="welcome-btn-primary"
              onClick={handleOpenProject}
              disabled={loading}
            >
              <FiFolder size={20} />
              <span>打开项目</span>
            </button>
            <button
              className="welcome-btn-secondary"
              onClick={handleCreateProject}
              disabled={loading}
            >
              <span className="btn-create-icon">+</span>
              <span>创建项目</span>
            </button>
          </div>
        )}

        {error && <div className="welcome-error">{error}</div>}
        {loading && <div className="welcome-loading">请稍候...</div>}
      </div>
    </div>
  );
}

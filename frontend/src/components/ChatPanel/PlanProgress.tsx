import type { PlanStep } from '../../types';
import './ChatPanel.css';

interface PlanProgressProps {
  title: string;
  steps: PlanStep[];
}

export default function PlanProgress({ title, steps }: PlanProgressProps) {
  const completed = steps.filter((s) => s.status === 'completed').length;
  const total = steps.length;
  const progress = total > 0 ? (completed / total) * 100 : 0;

  return (
    <div className="plan-progress">
      <div className="plan-header">
        <span className="plan-title">{title}</span>
        <span className="plan-count">{completed}/{total}</span>
      </div>

      <div className="plan-bar">
        <div className="plan-bar-fill" style={{ width: `${progress}%` }} />
      </div>

      <div className="plan-steps">
        {steps.map((step) => (
          <div key={step.index} className={`plan-step ${step.status}`}>
            <span className="plan-step-indicator">
              {step.status === 'completed' ? '✓' : step.index + 1}
            </span>
            <span className="plan-step-text">{step.description}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

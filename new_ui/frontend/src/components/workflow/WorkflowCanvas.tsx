import { useCallback, useState } from 'react';
import ReactFlow, {
  Node,
  Edge,
  Controls,
  MiniMap,
  Background,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  BackgroundVariant,
} from 'reactflow';
import 'reactflow/dist/style.css';
import WorkflowNode from './WorkflowNode';
import type { WorkflowStep } from '../../types/workflow';

interface WorkflowCanvasProps {
  steps: WorkflowStep[];
  currentStepIndex: number;
  onStepClick?: (stepId: string) => void;
}

const nodeTypes = {
  workflow: WorkflowNode,
};

export default function WorkflowCanvas({
  steps,
  currentStepIndex,
  onStepClick,
}: WorkflowCanvasProps) {
  // Convert steps to React Flow nodes
  const initialNodes: Node[] = steps.map((step, index) => ({
    id: step.id,
    type: 'workflow',
    position: { x: index * 200, y: 100 },
    data: {
      ...step,
      isActive: index === currentStepIndex,
      isCompleted: index < currentStepIndex,
      onClick: () => onStepClick?.(step.id),
    },
  }));

  // Create edges between consecutive nodes
  const initialEdges: Edge[] = steps.slice(0, -1).map((step, index) => ({
    id: `${step.id}-${steps[index + 1].id}`,
    source: step.id,
    target: steps[index + 1].id,
    animated: index === currentStepIndex - 1,
    style: {
      stroke:
        index < currentStepIndex
          ? '#10b981'
          : index === currentStepIndex - 1
          ? '#3b82f6'
          : '#d1d5db',
      strokeWidth: 2,
    },
  }));

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges]
  );

  return (
    <div className="h-[500px] rounded-xl border border-gray-200 bg-white overflow-hidden">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        fitView
        attributionPosition="bottom-left"
        className="bg-gray-50"
      >
        <Controls
          className="bg-white border border-gray-200 rounded-lg"
          showInteractive={false}
        />
        <MiniMap
          className="bg-white border border-gray-200 rounded-lg"
          nodeColor={(node) => {
            if (node.data.isCompleted) return '#10b981';
            if (node.data.isActive) return '#3b82f6';
            return '#d1d5db';
          }}
        />
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
      </ReactFlow>
    </div>
  );
}

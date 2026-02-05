import { useState } from 'react';
import { ChevronRight, ChevronDown, File, Folder, FolderOpen } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface FileNode {
  name: string;
  type: 'file' | 'folder';
  children?: FileNode[];
}

interface FileTreeProps {
  files: string[];
  onFileSelect?: (path: string) => void;
  selectedFile?: string;
}

export default function FileTree({ files, onFileSelect, selectedFile }: FileTreeProps) {
  // Convert flat file list to tree structure
  const buildTree = (paths: string[]): FileNode[] => {
    const root: Record<string, FileNode> = {};

    paths.forEach((path) => {
      const parts = path.split('/').filter(Boolean);
      let current = root;

      parts.forEach((part, index) => {
        const isFile = index === parts.length - 1;

        if (!current[part]) {
          current[part] = {
            name: part,
            type: isFile ? 'file' : 'folder',
            children: isFile ? undefined : {},
          };
        }

        if (!isFile) {
          current = current[part].children as Record<string, FileNode>;
        }
      });
    });

    const convertToArray = (obj: Record<string, FileNode>): FileNode[] => {
      return Object.values(obj).map((node) => ({
        ...node,
        children: node.children
          ? convertToArray(node.children as unknown as Record<string, FileNode>)
          : undefined,
      }));
    };

    return convertToArray(root);
  };

  const tree = buildTree(files);

  return (
    <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
      <div className="px-4 py-2 bg-gray-50 border-b border-gray-200">
        <span className="text-sm font-medium text-gray-700">Generated Files</span>
        <span className="text-xs text-gray-400 ml-2">({files.length})</span>
      </div>
      <div className="p-2 max-h-[400px] overflow-y-auto">
        {tree.length === 0 ? (
          <div className="py-8 text-center text-gray-400 text-sm">
            No files generated yet
          </div>
        ) : (
          tree.map((node) => (
            <TreeNode
              key={node.name}
              node={node}
              path=""
              onFileSelect={onFileSelect}
              selectedFile={selectedFile}
            />
          ))
        )}
      </div>
    </div>
  );
}

interface TreeNodeProps {
  node: FileNode;
  path: string;
  depth?: number;
  onFileSelect?: (path: string) => void;
  selectedFile?: string;
}

function TreeNode({
  node,
  path,
  depth = 0,
  onFileSelect,
  selectedFile,
}: TreeNodeProps) {
  const [isOpen, setIsOpen] = useState(depth < 2);
  const fullPath = path ? `${path}/${node.name}` : node.name;
  const isSelected = selectedFile === fullPath;

  const handleClick = () => {
    if (node.type === 'folder') {
      setIsOpen(!isOpen);
    } else {
      onFileSelect?.(fullPath);
    }
  };

  const getFileIcon = (filename: string) => {
    const ext = filename.split('.').pop()?.toLowerCase();
    const colors: Record<string, string> = {
      py: 'text-yellow-500',
      js: 'text-yellow-400',
      ts: 'text-blue-500',
      tsx: 'text-blue-400',
      json: 'text-green-500',
      md: 'text-gray-500',
      yaml: 'text-purple-500',
      yml: 'text-purple-500',
    };
    return colors[ext || ''] || 'text-gray-400';
  };

  return (
    <div>
      <button
        onClick={handleClick}
        className={`w-full flex items-center space-x-1.5 px-2 py-1.5 rounded text-sm hover:bg-gray-100 transition-colors ${
          isSelected ? 'bg-primary-50 text-primary-700' : 'text-gray-700'
        }`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {node.type === 'folder' ? (
          <>
            {isOpen ? (
              <ChevronDown className="h-4 w-4 text-gray-400 flex-shrink-0" />
            ) : (
              <ChevronRight className="h-4 w-4 text-gray-400 flex-shrink-0" />
            )}
            {isOpen ? (
              <FolderOpen className="h-4 w-4 text-yellow-500 flex-shrink-0" />
            ) : (
              <Folder className="h-4 w-4 text-yellow-500 flex-shrink-0" />
            )}
          </>
        ) : (
          <>
            <span className="w-4" />
            <File className={`h-4 w-4 flex-shrink-0 ${getFileIcon(node.name)}`} />
          </>
        )}
        <span className="truncate">{node.name}</span>
      </button>

      <AnimatePresence>
        {node.type === 'folder' && isOpen && node.children && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
          >
            {node.children.map((child) => (
              <TreeNode
                key={child.name}
                node={child}
                path={fullPath}
                depth={depth + 1}
                onFileSelect={onFileSelect}
                selectedFile={selectedFile}
              />
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

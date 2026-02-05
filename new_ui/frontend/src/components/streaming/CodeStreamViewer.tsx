import { useEffect, useRef } from 'react';
import Editor from '@monaco-editor/react';
import { motion } from 'framer-motion';
import { Code, Copy, Check, Loader2 } from 'lucide-react';
import { useState } from 'react';

interface CodeStreamViewerProps {
  code: string;
  currentFile: string | null;
  isStreaming: boolean;
  language?: string;
}

export default function CodeStreamViewer({
  code,
  currentFile,
  isStreaming,
  language = 'python',
}: CodeStreamViewerProps) {
  const [copied, setCopied] = useState(false);
  const editorRef = useRef<any>(null);

  // Auto-scroll to bottom when code updates
  useEffect(() => {
    if (editorRef.current && isStreaming) {
      const editor = editorRef.current;
      const model = editor.getModel();
      if (model) {
        const lineCount = model.getLineCount();
        editor.revealLine(lineCount);
      }
    }
  }, [code, isStreaming]);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const detectLanguage = (filename: string | null): string => {
    if (!filename) return language;
    const ext = filename.split('.').pop()?.toLowerCase();
    const langMap: Record<string, string> = {
      py: 'python',
      js: 'javascript',
      ts: 'typescript',
      tsx: 'typescript',
      jsx: 'javascript',
      md: 'markdown',
      json: 'json',
      yaml: 'yaml',
      yml: 'yaml',
      html: 'html',
      css: 'css',
      sh: 'shell',
      bash: 'shell',
    };
    return langMap[ext || ''] || language;
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-b border-gray-200">
        <div className="flex items-center space-x-2">
          <Code className="h-4 w-4 text-gray-500" />
          <span className="text-sm font-medium text-gray-700">
            {currentFile || 'Generated Code'}
          </span>
          {isStreaming && (
            <motion.span
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex items-center text-xs text-primary-600"
            >
              <Loader2 className="h-3 w-3 mr-1 animate-spin" />
              Generating...
            </motion.span>
          )}
        </div>

        <button
          onClick={handleCopy}
          disabled={!code}
          className="flex items-center space-x-1 px-2 py-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded transition-colors disabled:opacity-50"
        >
          {copied ? (
            <>
              <Check className="h-3 w-3 text-green-500" />
              <span>Copied!</span>
            </>
          ) : (
            <>
              <Copy className="h-3 w-3" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>

      {/* Editor */}
      <div className="relative">
        {!code && !isStreaming ? (
          <div className="h-[400px] flex items-center justify-center text-gray-400">
            <div className="text-center">
              <Code className="h-12 w-12 mx-auto mb-3 opacity-50" />
              <p className="text-sm">Code will appear here</p>
            </div>
          </div>
        ) : (
          <Editor
            height="400px"
            language={detectLanguage(currentFile)}
            value={code}
            theme="vs-light"
            onMount={(editor) => {
              editorRef.current = editor;
            }}
            options={{
              readOnly: true,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              fontSize: 13,
              fontFamily: "'JetBrains Mono', Menlo, Monaco, monospace",
              lineNumbers: 'on',
              renderLineHighlight: 'none',
              scrollbar: {
                vertical: 'auto',
                horizontal: 'auto',
              },
              padding: { top: 16, bottom: 16 },
            }}
          />
        )}

        {/* Streaming indicator overlay */}
        {isStreaming && (
          <div className="absolute bottom-4 right-4">
            <motion.div
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              className="flex items-center space-x-2 px-3 py-1.5 bg-primary-50 border border-primary-200 rounded-full"
            >
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary-400 opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-primary-500"></span>
              </span>
              <span className="text-xs font-medium text-primary-700">
                Live
              </span>
            </motion.div>
          </div>
        )}
      </div>
    </div>
  );
}

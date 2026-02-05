import Editor from '@monaco-editor/react';
import { Code } from 'lucide-react';

interface CodePreviewProps {
  code: string;
  filename?: string;
  language?: string;
}

export default function CodePreview({
  code,
  filename,
  language = 'python',
}: CodePreviewProps) {
  const detectLanguage = (fname?: string): string => {
    if (!fname) return language;
    const ext = fname.split('.').pop()?.toLowerCase();
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
    };
    return langMap[ext || ''] || language;
  };

  return (
    <div className="rounded-lg border border-gray-200 bg-white overflow-hidden">
      <div className="flex items-center space-x-2 px-4 py-2 bg-gray-50 border-b border-gray-200">
        <Code className="h-4 w-4 text-gray-500" />
        <span className="text-sm font-medium text-gray-700">
          {filename || 'Preview'}
        </span>
      </div>
      {code ? (
        <Editor
          height="300px"
          language={detectLanguage(filename)}
          value={code}
          theme="vs-light"
          options={{
            readOnly: true,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 13,
            fontFamily: "'JetBrains Mono', monospace",
            padding: { top: 16, bottom: 16 },
          }}
        />
      ) : (
        <div className="h-[300px] flex items-center justify-center text-gray-400">
          Select a file to preview
        </div>
      )}
    </div>
  );
}

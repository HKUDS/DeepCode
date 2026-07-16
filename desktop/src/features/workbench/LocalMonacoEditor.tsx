import Editor, { loader, type EditorProps } from "@monaco-editor/react";
import * as monaco from "monaco-editor/esm/vs/editor/editor.api.js";
import editorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import "monaco-editor/esm/vs/basic-languages/css/css.contribution.js";
import "monaco-editor/esm/vs/basic-languages/html/html.contribution.js";
import "monaco-editor/esm/vs/basic-languages/ini/ini.contribution.js";
import "monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution.js";
import "monaco-editor/esm/vs/basic-languages/markdown/markdown.contribution.js";
import "monaco-editor/esm/vs/basic-languages/python/python.contribution.js";
import "monaco-editor/esm/vs/basic-languages/rust/rust.contribution.js";
import "monaco-editor/esm/vs/basic-languages/typescript/typescript.contribution.js";
import "monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution.js";

const workerEnvironment: monaco.Environment = {
  getWorker(_moduleId: string, label: string) {
    void label;
    return new editorWorker();
  },
};

(globalThis as typeof globalThis & {
  MonacoEnvironment?: monaco.Environment;
}).MonacoEnvironment = workerEnvironment;

loader.config({ monaco });

export default function LocalMonacoEditor(props: EditorProps) {
  return <Editor {...props} />;
}

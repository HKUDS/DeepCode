import { useState } from "react";

import type {
  Artifact,
  WorkflowRun,
} from "../../generated/app-server";
import type { DesktopRuntime } from "../../rpc/contracts";
import { InspectorEmpty } from "./InspectorEmpty";
import { formatBytes } from "./inspectorFormat";
import styles from "./Inspector.module.css";

interface ArtifactPreview {
  id: string;
  content: string | null;
  directory: boolean;
  truncated: boolean;
}

interface ArtifactsPanelProps {
  runtime: DesktopRuntime;
  workflow: WorkflowRun | null;
  artifacts: Artifact[];
}

export function ArtifactsPanel({
  runtime,
  workflow,
  artifacts,
}: ArtifactsPanelProps) {
  const [preview, setPreview] = useState<ArtifactPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  const previewArtifact = async (artifact: Artifact) => {
    setError(null);
    try {
      const result = await runtime.request("artifact/read", {
        artifactId: artifact.id,
      });
      setPreview({
        id: artifact.id,
        content: result.content,
        directory: result.directory,
        truncated: result.truncated,
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  return (
    <div className={styles.content}>
      <div className={styles.heading}>
        <div>
          <p className={styles.eyebrow}>Workflow outputs</p>
          <h2>{workflow ? `Attempt ${workflow.attempt}` : "Artifacts"}</h2>
        </div>
        {workflow ? (
          <span className={styles.runStatus} data-status={workflow.status}>
            {workflow.status}
          </span>
        ) : null}
      </div>
      {workflow?.errorMessage ? (
        <p className={styles.panelError}>{workflow.errorMessage}</p>
      ) : null}
      {error ? <p className={styles.panelError}>{error}</p> : null}
      {artifacts.length ? (
        <div className={styles.artifactList}>
          {artifacts.map((artifact) => (
            <button
              type="button"
              key={artifact.id}
              data-active={preview?.id === artifact.id}
              onClick={() => void previewArtifact(artifact)}
            >
              <span>{artifact.kind}</span>
              <strong>{artifact.name}</strong>
              <small>{formatBytes(artifact.byteSize)}</small>
            </button>
          ))}
        </div>
      ) : (
        <InspectorEmpty label="Verified workflow outputs will appear here." compact />
      )}
      {preview ? (
        <div className={styles.artifactPreview}>
          {preview.directory ? (
            <p>This artifact is a generated code directory. Browse it from Files.</p>
          ) : preview.content === null ? (
            <p>Binary artifacts are recorded but not rendered as text.</p>
          ) : (
            <pre>{preview.content}</pre>
          )}
          {preview.truncated ? <small>Preview truncated at 128 KiB.</small> : null}
        </div>
      ) : null}
    </div>
  );
}

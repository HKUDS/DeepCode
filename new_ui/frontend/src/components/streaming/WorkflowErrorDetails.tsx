import type { WorkflowErrorDetails as WorkflowErrorDetailsType } from "../../types/api";

interface WorkflowErrorDetailsProps {
  error: string;
  details?: WorkflowErrorDetailsType | null;
}

function labelForCategory(category?: string): string {
  const labels: Record<string, string> = {
    provider_timeout: "Provider timeout",
    timeout: "Timeout",
    rate_limit: "Rate limit",
    quota_or_billing: "Quota or billing",
    llm_provider: "LLM provider",
    document_preprocessing: "Document preprocessing",
    workflow: "Workflow",
  };
  return category ? labels[category] || category : "Workflow";
}

export function WorkflowErrorDetails({
  error,
  details,
}: WorkflowErrorDetailsProps) {
  const logEntries = details?.log_paths
    ? Object.entries(details.log_paths)
    : [];

  return (
    <div className="space-y-3">
      <p className="text-sm text-red-700 whitespace-pre-wrap">{error}</p>

      {details && (
        <div className="rounded-md border border-red-200 bg-white/70 p-3 text-xs text-red-900">
          <div className="grid gap-2 sm:grid-cols-2">
            <div>
              <div className="font-medium text-red-950">Category</div>
              <div>{labelForCategory(details.category)}</div>
            </div>
            {details.stage && (
              <div>
                <div className="font-medium text-red-950">Failed stage</div>
                <div>{details.stage}</div>
              </div>
            )}
            {typeof details.progress === "number" && (
              <div>
                <div className="font-medium text-red-950">Progress</div>
                <div>{details.progress}%</div>
              </div>
            )}
            {details.error_type && (
              <div>
                <div className="font-medium text-red-950">Error type</div>
                <div>{details.error_type}</div>
              </div>
            )}
            {details.task_short_id && (
              <div>
                <div className="font-medium text-red-950">Task</div>
                <div className="font-mono">{details.task_short_id}</div>
              </div>
            )}
            {details.log_stream_url && (
              <div>
                <div className="font-medium text-red-950">Log stream</div>
                <div className="font-mono">{details.log_stream_url}</div>
              </div>
            )}
          </div>

          {details.hint && (
            <div className="mt-3 border-t border-red-100 pt-3">
              <div className="font-medium text-red-950">What to check</div>
              <div className="mt-1">{details.hint}</div>
            </div>
          )}

          {logEntries.length > 0 && (
            <div className="mt-3 border-t border-red-100 pt-3">
              <div className="font-medium text-red-950">Task logs</div>
              <div className="mt-1 space-y-1">
                {logEntries.map(([channel, path]) => (
                  <div key={channel} className="break-all font-mono">
                    {channel}: {path}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

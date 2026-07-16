import {
  Folder,
  FolderClock,
  FolderOpen,
  MessageSquare,
  Plug,
  Plus,
  Search,
  Settings,
  ShieldAlert,
  Sparkles,
  WandSparkles,
} from "lucide-react";
import { useEffect, useMemo, useRef } from "react";

import {
  isRecoveredHistoryProject,
  projectCanExecute,
} from "../../app/projectPresentation";
import type { Project, Thread } from "../../generated/app-server";
import type { DesktopDestination } from "../../app/useDesktopUi";
import type { SidecarStatus } from "../../rpc/contracts";
import { SessionRow } from "./SessionRow";
import styles from "./DesktopSidebar.module.css";

interface DesktopSidebarProps {
  projects: Project[];
  threads: Thread[];
  selectedProjectId: string | null;
  selectedThreadId: string | null;
  query: string;
  busy: boolean;
  runtime: SidecarStatus;
  destination: DesktopDestination;
  onDestination(destination: DesktopDestination): void;
  onQueryChange(query: string): void;
  onOpenProject(): void;
  onSelectProject(projectId: string): void;
  onCreateThread(): void;
  onSelectThread(threadId: string): void;
  onRenameThread(threadId: string, title: string): Promise<void>;
  onArchiveThread(threadId: string): Promise<void>;
}

interface ThreadBucket {
  label: string;
  threads: Thread[];
}

interface SidebarProjectGroup {
  key: string;
  project: Project | null;
  displayName: string;
  subtitle: string;
  threads: Thread[];
  active: boolean;
}

function threadBuckets(threads: Thread[]): ThreadBucket[] {
  const now = Date.now();
  const day = 24 * 60 * 60 * 1000;
  const buckets: ThreadBucket[] = [
    { label: "Today", threads: [] },
    { label: "Previous 7 days", threads: [] },
    { label: "Older", threads: [] },
  ];
  for (const thread of threads) {
    const age = Math.max(0, now - new Date(thread.updatedAt).getTime());
    const bucket = age < day ? buckets[0] : age < day * 7 ? buckets[1] : buckets[2];
    bucket.threads.push(thread);
  }
  return buckets.filter((bucket) => bucket.threads.length > 0);
}

function matches(
  project: Project,
  thread: Thread,
  normalizedQuery: string,
): boolean {
  if (!normalizedQuery) return true;
  return [project.displayName, project.canonicalPath, thread.title, thread.workspacePath]
    .join("\n")
    .toLocaleLowerCase()
    .includes(normalizedQuery);
}

function projectSubtitle(project: Project): string {
  const segments = project.canonicalPath.split("/").filter(Boolean);
  if (segments.length < 2) return project.canonicalPath;
  return `…/${segments.slice(-2).join("/")}`;
}

export function DesktopSidebar({
  projects,
  threads,
  selectedProjectId,
  selectedThreadId,
  query,
  busy,
  runtime,
  destination,
  onDestination,
  onQueryChange,
  onOpenProject,
  onSelectProject,
  onCreateThread,
  onSelectThread,
  onRenameThread,
  onArchiveThread,
}: DesktopSidebarProps) {
  const searchRef = useRef<HTMLInputElement | null>(null);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const selectedProject =
    projects.find((project) => project.id === selectedProjectId) ?? null;
  const canCreateThread = projectCanExecute(selectedProject);
  const projectGroups = useMemo<SidebarProjectGroup[]>(() => {
    const projectById = new Map(projects.map((project) => [project.id, project]));
    const recoveredProjectIds = new Set(
      projects
        .filter(isRecoveredHistoryProject)
        .map((project) => project.id),
    );
    const regularGroups: SidebarProjectGroup[] = projects
      .filter((project) => !recoveredProjectIds.has(project.id))
      .map((project) => {
        const projectThreads = threads
          .filter((thread) => thread.projectId === project.id)
          .filter((thread) => matches(project, thread, normalizedQuery))
          .sort((left, right) =>
            right.updatedAt.localeCompare(left.updatedAt),
          );
        return {
          key: project.id,
          project,
          displayName: project.displayName,
          subtitle: projectSubtitle(project),
          threads: projectThreads,
          active: project.id === selectedProjectId,
        };
      })
      .filter((group) => !normalizedQuery || group.threads.length > 0);

    const recoveredThreads = threads
      .filter((thread) => recoveredProjectIds.has(thread.projectId))
      .filter((thread) => {
        const project = projectById.get(thread.projectId);
        return project ? matches(project, thread, normalizedQuery) : false;
      })
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
    if (recoveredThreads.length > 0) {
      regularGroups.push({
        key: "recovered-history",
        project: null,
        displayName: "Previous sessions",
        subtitle: "Original folders unavailable",
        threads: recoveredThreads,
        active: recoveredThreads.some(
          (thread) => thread.projectId === selectedProjectId,
        ),
      });
    }
    return regularGroups;
  }, [normalizedQuery, projects, selectedProjectId, threads]);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey) return;
      if (event.key.toLocaleLowerCase() === "k") {
        event.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      }
      if (
        event.key.toLocaleLowerCase() === "n" &&
        canCreateThread &&
        !busy
      ) {
        event.preventDefault();
        onCreateThread();
      }
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [busy, canCreateThread, onCreateThread]);

  return (
    <aside className={styles.sidebar} aria-label="Projects and Sessions">
      <div className={styles.brandRow}>
        <span className={styles.brandMark} aria-hidden="true">
          <i />
          <i />
        </span>
        <span className={styles.brandCopy}>
          <strong>DeepCode</strong>
          <small>Local agent</small>
        </span>
      </div>

      {destination === "threads" ? (
        <>
          <button
            className={styles.newThread}
            type="button"
            onClick={onCreateThread}
            disabled={busy || !canCreateThread}
          >
            <Plus size={16} strokeWidth={1.9} />
            New thread
            <kbd>⌘N</kbd>
          </button>

          <label className={styles.search}>
            <Search size={15} strokeWidth={1.8} aria-hidden="true" />
            <span className={styles.srOnly}>Search Sessions</span>
            <input
              ref={searchRef}
              value={query}
              onChange={(event) => onQueryChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  onQueryChange("");
                  event.currentTarget.blur();
                }
              }}
              placeholder="Search Sessions"
              spellCheck={false}
            />
            <kbd>⌘K</kbd>
          </label>
        </>
      ) : null}

      <nav className={styles.destinations} aria-label="DeepCode destinations">
        <button
          type="button"
          data-active={destination === "threads"}
          onClick={() => onDestination("threads")}
        >
          <MessageSquare size={15} />
          Threads
        </button>
        <button
          type="button"
          data-active={destination === "automations"}
          onClick={() => onDestination("automations")}
        >
          <Sparkles size={15} />
          Automations
        </button>
        <button
          type="button"
          data-active={destination === "skills"}
          onClick={() => onDestination("skills")}
        >
          <WandSparkles size={15} />
          Skills &amp; Hooks
        </button>
        <button
          type="button"
          data-active={destination === "mcp"}
          onClick={() => onDestination("mcp")}
        >
          <Plug size={15} />
          MCP
        </button>
        <button
          type="button"
          data-active={destination === "settings"}
          onClick={() => onDestination("settings")}
        >
          <Settings size={15} />
          Settings
        </button>
      </nav>

      <div className={styles.sectionHeading}>
        <span>Projects</span>
        <button
          type="button"
          onClick={onOpenProject}
          disabled={busy}
          aria-label="Open project folder"
          title="Open project folder"
        >
          <Plus size={15} />
        </button>
      </div>

      <nav className={styles.projectList} aria-label="Session history">
        {projects.length === 0 ? (
          <button className={styles.emptyProject} type="button" onClick={onOpenProject}>
            <FolderOpen size={18} />
            <span>
              <strong>Open a local folder</strong>
              <small>Your CLI Sessions will appear here.</small>
            </span>
          </button>
        ) : normalizedQuery && projectGroups.length === 0 ? (
          <div className={styles.noResults}>
            <Search size={16} />
            <strong>No matching Sessions</strong>
            <small>Search by title, project, or workspace path.</small>
          </div>
        ) : (
          projectGroups.map((group) => {
            const project = group.project;
            return (
              <section className={styles.projectGroup} key={group.key}>
                {project ? (
                  <button
                    type="button"
                    className={styles.projectButton}
                    data-active={group.active}
                    onClick={() => onSelectProject(project.id)}
                    title={project.canonicalPath}
                  >
                    {group.active ? (
                      <FolderOpen size={15} />
                    ) : (
                      <Folder size={15} />
                    )}
                    <span>
                      <strong>{group.displayName}</strong>
                      <small>{group.subtitle}</small>
                    </span>
                    {project.trustState === "untrusted" ? (
                      <ShieldAlert
                        size={14}
                        className={styles.untrusted}
                        aria-label="Project not trusted"
                      />
                    ) : null}
                  </button>
                ) : (
                  <div
                    className={styles.projectButton}
                    data-active={group.active}
                    data-static="true"
                  >
                    <FolderClock size={15} />
                    <span>
                      <strong>{group.displayName}</strong>
                      <small>{group.subtitle}</small>
                    </span>
                  </div>
                )}

                {threadBuckets(group.threads).map((bucket) => (
                  <div className={styles.threadBucket} key={bucket.label}>
                    <p>{bucket.label}</p>
                    {bucket.threads.map((thread) => (
                      <SessionRow
                        key={thread.id}
                        thread={thread}
                        active={thread.id === selectedThreadId}
                        busy={busy}
                        onSelect={onSelectThread}
                        onRename={onRenameThread}
                        onArchive={onArchiveThread}
                      />
                    ))}
                  </div>
                ))}
              </section>
            );
          })
        )}
      </nav>

      <footer className={styles.footer}>
        <Sparkles size={14} aria-hidden="true" />
        <span>
          <strong>{runtime.phase === "ready" ? "Local agent ready" : runtime.phase}</strong>
          <small>Shared Session history</small>
        </span>
        <span className={styles.runtimeDot} data-phase={runtime.phase} aria-hidden="true" />
      </footer>
    </aside>
  );
}

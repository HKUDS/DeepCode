import {
  ChevronRight,
  Cable,
  Folder,
  FolderClock,
  FolderOpen,
  MessageSquare,
  Plus,
  Puzzle,
  Search,
  Settings,
  ShieldAlert,
  Sparkles,
  WandSparkles,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  isRecoveredHistoryProject,
  projectCanExecute,
} from "../../app/projectPresentation";
import type { Project, Thread } from "../../generated/app-server";
import type { DesktopDestination } from "../../app/useDesktopUi";
import type { SidecarStatus } from "../../rpc/contracts";
import { SessionRow } from "./SessionRow";
import { useProjectDisclosure } from "./useProjectDisclosure";
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
  settingsOpen: boolean;
  onDestination(destination: DesktopDestination): void;
  onOpenSettings(): void;
  onQueryChange(query: string): void;
  onOpenProject(): void;
  onSelectProject(projectId: string): void;
  onCreateThread(): void;
  onSelectThread(threadId: string): void;
  onRenameThread(threadId: string, title: string): Promise<void>;
  onArchiveThread(threadId: string): Promise<void>;
  onDeleteThread(threadId: string): Promise<void>;
}

interface SidebarProjectGroup {
  key: string;
  project: Project | null;
  displayName: string;
  description: string;
  threads: Thread[];
  active: boolean;
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

function projectDescription(project: Project): string {
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
  settingsOpen,
  onDestination,
  onOpenSettings,
  onQueryChange,
  onOpenProject,
  onSelectProject,
  onCreateThread,
  onSelectThread,
  onRenameThread,
  onArchiveThread,
  onDeleteThread,
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
          description: projectDescription(project),
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
        description: "Original folders unavailable",
        threads: recoveredThreads,
        active: recoveredThreads.some(
          (thread) => thread.projectId === selectedProjectId,
        ),
      });
    }
    return regularGroups;
  }, [normalizedQuery, projects, selectedProjectId, threads]);
  const activeGroupKey =
    projectGroups.find((group) => group.active)?.key ?? null;
  const disclosure = useProjectDisclosure(activeGroupKey);

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
          {/* Three rules stepping inward: nesting, and so depth. The tile takes
              its fill from --text-primary and the mark from --text-inverse, so
              the pair swaps itself in the dark theme. */}
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M4.75 7.5h14.5" opacity="1" />
            <path d="M9 12h10.25" opacity="0.72" />
            <path d="M13.25 16.5h6" opacity="0.44" />
          </svg>
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
            <Search size={16} strokeWidth={1.8} aria-hidden="true" />
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
          <MessageSquare size={16} />
          Threads
        </button>
        <button
          type="button"
          data-active={destination === "automations"}
          onClick={() => onDestination("automations")}
        >
          <Sparkles size={16} />
          Automations
        </button>
        <button
          type="button"
          data-active={destination === "skills"}
          onClick={() => onDestination("skills")}
        >
          <WandSparkles size={16} />
          Skills
        </button>
        <button
          type="button"
          data-active={destination === "plugins"}
          onClick={() => onDestination("plugins")}
        >
          <Puzzle size={16} />
          Plugins
        </button>
        <button
          type="button"
          data-active={destination === "mcp"}
          onClick={() => onDestination("mcp")}
        >
          <Cable size={16} />
          MCP
        </button>
        <button
          type="button"
          data-active={settingsOpen}
          onClick={onOpenSettings}
        >
          <Settings size={16} />
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
          <Plus size={16} />
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
            const expanded =
              Boolean(normalizedQuery) || disclosure.isExpanded(group.key);
            return (
              <section className={styles.projectGroup} key={group.key}>
                <button
                  type="button"
                  className={styles.projectButton}
                  data-active={group.active}
                  data-static={!project || undefined}
                  aria-expanded={expanded}
                  aria-controls={`project-sessions-${group.key}`}
                  onClick={() => {
                    if (project && project.id !== selectedProjectId) {
                      disclosure.expand(group.key);
                      onSelectProject(project.id);
                    } else {
                      disclosure.toggle(group.key);
                    }
                  }}
                  title={project?.canonicalPath ?? group.description}
                >
                  <ChevronRight
                    size={14}
                    className={styles.disclosure}
                    aria-hidden="true"
                  />
                  {project ? (
                    group.active ? (
                      <FolderOpen size={16} />
                    ) : (
                      <Folder size={16} />
                    )
                  ) : (
                    <FolderClock size={16} />
                  )}
                  <strong>{group.displayName}</strong>
                  {project?.trustState === "untrusted" ? (
                    <ShieldAlert
                      size={14}
                      className={styles.untrusted}
                      aria-label="Project not trusted"
                    />
                  ) : null}
                </button>

                <ProjectSessions
                  id={`project-sessions-${group.key}`}
                  expanded={expanded}
                  threads={group.threads}
                  selectedThreadId={selectedThreadId}
                  searching={Boolean(normalizedQuery)}
                  busy={busy}
                  onSelectThread={(threadId) => {
                    disclosure.expand(group.key);
                    onSelectThread(threadId);
                  }}
                  onRenameThread={onRenameThread}
                  onArchiveThread={onArchiveThread}
                  onDeleteThread={onDeleteThread}
                />
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

const SESSION_PREVIEW_LIMIT = 6;

interface ProjectSessionsProps {
  id: string;
  expanded: boolean;
  threads: Thread[];
  selectedThreadId: string | null;
  searching: boolean;
  busy: boolean;
  onSelectThread(threadId: string): void;
  onRenameThread(threadId: string, title: string): Promise<void>;
  onArchiveThread(threadId: string): Promise<void>;
  onDeleteThread(threadId: string): Promise<void>;
}

function ProjectSessions({
  id,
  expanded,
  threads,
  selectedThreadId,
  searching,
  busy,
  onSelectThread,
  onRenameThread,
  onArchiveThread,
  onDeleteThread,
}: ProjectSessionsProps) {
  const [showAll, setShowAll] = useState(false);

  if (!expanded) return null;

  const visibleThreads =
    searching || showAll
      ? threads
      : threads.slice(0, SESSION_PREVIEW_LIMIT);
  if (
    !searching &&
    !showAll &&
    selectedThreadId &&
    !visibleThreads.some((thread) => thread.id === selectedThreadId)
  ) {
    const selected = threads.find((thread) => thread.id === selectedThreadId);
    if (selected) {
      visibleThreads.splice(Math.max(0, SESSION_PREVIEW_LIMIT - 1), 1, selected);
    }
  }
  const hiddenCount = threads.length - visibleThreads.length;

  return (
    <div className={styles.threadList} id={id}>
      {threads.length === 0 ? (
        <p className={styles.noSessions}>No Sessions</p>
      ) : (
        visibleThreads.map((thread) => (
          <SessionRow
            key={thread.id}
            thread={thread}
            active={thread.id === selectedThreadId}
            busy={busy}
            onSelect={onSelectThread}
            onRename={onRenameThread}
            onArchive={onArchiveThread}
            onDelete={onDeleteThread}
          />
        ))
      )}
      {!searching && threads.length > SESSION_PREVIEW_LIMIT ? (
        <button
          type="button"
          className={styles.showMore}
          onClick={() => setShowAll((current) => !current)}
        >
          {showAll ? "Show less" : `Show ${hiddenCount} more`}
        </button>
      ) : null}
    </div>
  );
}

/**
 * WorkspacePicker — a folder browser modal for choosing a chat's workspace.
 *
 * Navigates the restricted /agent/fs/dirs listing (directories only, fenced
 * under $HOME). Double-click / click a folder to descend, "Up" to ascend,
 * "Use this folder" to select the current directory.
 */
import { useCallback, useEffect, useState } from 'react'
import { ArrowUp, Folder, FolderOpen, Home, Loader2, X } from 'lucide-react'
import api from '../../services/api'

interface DirEntry {
  name: string
  path: string
}
interface Listing {
  path: string
  parent: string | null
  home: string
  dirs: DirEntry[]
}

interface Props {
  onSelect: (path: string) => void
  onClose: () => void
}

export default function WorkspacePicker({ onSelect, onClose }: Props) {
  const [listing, setListing] = useState<Listing | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async (path: string) => {
    setLoading(true)
    setError('')
    try {
      const res = await api.get('/agent/fs/dirs', { params: path ? { path } : {} })
      setListing(res.data)
    } catch {
      setError('Could not read that folder.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load('')
  }, [load])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[70vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl bg-white shadow-xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3 dark:border-gray-800">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <FolderOpen size={16} className="text-blue-500" /> Choose a workspace folder
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700">
            <X size={18} />
          </button>
        </div>

        {/* Path bar */}
        <div className="flex items-center gap-2 border-b border-gray-100 px-4 py-2 dark:border-gray-800">
          <button
            title="Home"
            onClick={() => load('')}
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 dark:hover:bg-gray-800"
          >
            <Home size={15} />
          </button>
          <button
            title="Up"
            disabled={!listing?.parent}
            onClick={() => listing?.parent && load(listing.parent)}
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:opacity-30 dark:hover:bg-gray-800"
          >
            <ArrowUp size={15} />
          </button>
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-gray-500" title={listing?.path}>
            {listing?.path ?? '…'}
          </span>
        </div>

        {/* Directory list */}
        <div className="min-h-[12rem] flex-1 overflow-y-auto p-2">
          {loading ? (
            <div className="flex items-center gap-2 p-4 text-sm text-gray-400">
              <Loader2 size={14} className="animate-spin" /> loading…
            </div>
          ) : error ? (
            <p className="p-4 text-sm text-red-500">{error}</p>
          ) : listing && listing.dirs.length === 0 ? (
            <p className="p-4 text-sm text-gray-400">No sub-folders here.</p>
          ) : (
            listing?.dirs.map((d) => (
              <button
                key={d.path}
                onClick={() => load(d.path)}
                className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
              >
                <Folder size={15} className="shrink-0 text-blue-500" />
                <span className="truncate">{d.name}</span>
              </button>
            ))
          )}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-gray-200 px-4 py-3 dark:border-gray-800">
          <button
            onClick={onClose}
            className="rounded-lg px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            Cancel
          </button>
          <button
            disabled={!listing}
            onClick={() => listing && onSelect(listing.path)}
            className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
          >
            Use this folder
          </button>
        </div>
      </div>
    </div>
  )
}

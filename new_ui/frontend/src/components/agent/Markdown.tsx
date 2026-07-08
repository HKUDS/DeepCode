/**
 * Markdown — assistant replies rendered with GitHub-flavored markdown.
 *
 * Tailwind-styled renderers for the elements agents actually emit: fenced
 * code blocks (scrollable, monospace), inline code, lists, links, tables,
 * headings. Kept dependency-light on top of react-markdown + remark-gfm.
 */
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'

const components: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
  a: ({ children, href }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="text-blue-600 dark:text-blue-400 underline underline-offset-2"
    >
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="mb-2 ml-5 list-disc space-y-1">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 ml-5 list-decimal space-y-1">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  h1: ({ children }) => <h1 className="mb-2 mt-3 text-lg font-bold">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-2 mt-3 text-base font-bold">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1 mt-2 text-sm font-bold">{children}</h3>,
  blockquote: ({ children }) => (
    <blockquote className="mb-2 border-l-2 border-gray-300 dark:border-gray-700 pl-3 text-gray-600 dark:text-gray-400">
      {children}
    </blockquote>
  ),
  // react-markdown v9 dropped the `inline` prop. Fenced blocks arrive wrapped
  // in <pre> (styled below) and carry a language class or a newline; inline
  // code is a bare single-line <code>. Distinguish on that.
  code({ className, children, ...props }: any) {
    const text = String(children ?? '')
    const isBlock = /language-/.test(className ?? '') || text.includes('\n')
    if (isBlock) {
      return (
        <code className={`font-mono text-xs ${className ?? ''}`} {...props}>
          {children}
        </code>
      )
    }
    return (
      <code
        className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[0.85em] dark:bg-gray-800"
        {...props}
      >
        {children}
      </code>
    )
  },
  pre: ({ children }) => (
    <div className="my-2 overflow-x-auto rounded-lg bg-gray-900 p-3 text-gray-100 dark:bg-black/60">
      {children}
    </div>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto">
      <table className="min-w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-gray-200 dark:border-gray-700 px-2 py-1 text-left font-semibold">
      {children}
    </th>
  ),
  td: ({ children }) => (
    <td className="border border-gray-200 dark:border-gray-700 px-2 py-1">{children}</td>
  ),
}

export default function Markdown({ children }: { children: string }) {
  return (
    <div className="text-sm text-gray-800 dark:text-gray-200">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  )
}

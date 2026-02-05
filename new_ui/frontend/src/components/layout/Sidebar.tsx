import { Link, useLocation } from 'react-router-dom';
import {
  FileText,
  MessageSquare,
  GitBranch,
  Clock,
  Folder,
} from 'lucide-react';
import { useSessionStore } from '../../stores/sessionStore';

export default function Sidebar() {
  const location = useLocation();
  const { recentProjects } = useSessionStore();

  const menuItems = [
    {
      path: '/paper-to-code',
      icon: FileText,
      label: 'Paper to Code',
      description: 'Convert research papers',
    },
    {
      path: '/chat',
      icon: MessageSquare,
      label: 'Chat Planning',
      description: 'Describe your project',
    },
    {
      path: '/workflow',
      icon: GitBranch,
      label: 'Workflow Editor',
      description: 'Visual workflow design',
    },
  ];

  return (
    <aside className="hidden lg:flex flex-col w-64 min-h-[calc(100vh-4rem)] border-r border-gray-200 bg-white">
      <div className="flex-1 p-4">
        {/* Quick Actions */}
        <div className="mb-6">
          <h3 className="px-3 text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
            Quick Actions
          </h3>
          <nav className="space-y-1">
            {menuItems.map((item) => {
              const Icon = item.icon;
              const isActive = location.pathname === item.path;

              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={`flex items-start space-x-3 px-3 py-2.5 rounded-lg transition-colors ${
                    isActive
                      ? 'bg-primary-50 text-primary-700'
                      : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
                  }`}
                >
                  <Icon
                    className={`h-5 w-5 mt-0.5 ${
                      isActive ? 'text-primary-600' : 'text-gray-400'
                    }`}
                  />
                  <div>
                    <div className="font-medium text-sm">{item.label}</div>
                    <div
                      className={`text-xs ${
                        isActive ? 'text-primary-600/70' : 'text-gray-400'
                      }`}
                    >
                      {item.description}
                    </div>
                  </div>
                </Link>
              );
            })}
          </nav>
        </div>

        {/* Recent Projects */}
        {recentProjects.length > 0 && (
          <div>
            <h3 className="px-3 text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2 flex items-center">
              <Clock className="h-3 w-3 mr-1.5" />
              Recent
            </h3>
            <div className="space-y-1">
              {recentProjects.slice(0, 5).map((project) => (
                <button
                  key={project.id}
                  className="w-full flex items-center space-x-3 px-3 py-2 rounded-lg text-left text-sm text-gray-600 hover:bg-gray-50 hover:text-gray-900 transition-colors"
                >
                  <Folder className="h-4 w-4 text-gray-400" />
                  <span className="truncate">{project.name}</span>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="p-4 border-t border-gray-100">
        <div className="flex items-center justify-center space-x-2 text-xs text-gray-400">
          <img
            src="https://github.com/Zongwei9888/Experiment_Images/raw/43c585dca3d21b8e4b6390d835cdd34dc4b4b23d/DeepCode_images/title_logo.svg"
            alt="DeepCode"
            className="h-4 w-4"
          />
          <span>DeepCode v1.0.0</span>
        </div>
      </div>
    </aside>
  );
}

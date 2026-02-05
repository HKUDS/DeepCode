import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Settings, Menu, Loader2 } from 'lucide-react';
import { useState } from 'react';
import { useWorkflowStore } from '../../stores/workflowStore';

export default function Header() {
  const location = useLocation();
  const navigate = useNavigate();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

  const { status, workflowType, progress } = useWorkflowStore();
  const isRunning = status === 'running';

  const navItems = [
    { path: '/', label: 'Home' },
    { path: '/paper-to-code', label: 'Paper to Code' },
    { path: '/chat', label: 'Chat Planning' },
    { path: '/workflow', label: 'Workflow' },
  ];

  return (
    <header className="sticky top-0 z-50 border-b border-gray-200 bg-white/80 backdrop-blur-sm">
      <div className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8">
        <div className="flex h-16 items-center justify-between">
          {/* Logo */}
          <Link to="/" className="flex items-center space-x-2">
            <img
              src="https://github.com/Zongwei9888/Experiment_Images/raw/43c585dca3d21b8e4b6390d835cdd34dc4b4b23d/DeepCode_images/title_logo.svg"
              alt="DeepCode Logo"
              className="h-8 w-8"
            />
            <span className="text-xl font-semibold text-gray-900">
              DeepCode
            </span>
          </Link>

          {/* Desktop Navigation */}
          <nav className="hidden md:flex items-center space-x-1">
            {navItems.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  location.pathname === item.path
                    ? 'bg-primary-50 text-primary-600'
                    : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
                }`}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          {/* Right Side */}
          <div className="flex items-center space-x-3">
            {/* Running Task Indicator */}
            {isRunning && (
              <button
                onClick={() => {
                  if (workflowType === 'chat-planning') {
                    navigate('/chat');
                  } else if (workflowType === 'paper-to-code') {
                    navigate('/paper-to-code');
                  }
                }}
                className="flex items-center space-x-2 px-3 py-1.5 bg-blue-50 border border-blue-200 rounded-full text-sm font-medium text-blue-700 hover:bg-blue-100 transition-colors"
              >
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="hidden sm:inline">Task Running</span>
                <span className="text-blue-500">{progress}%</span>
              </button>
            )}

            <Link
              to="/settings"
              className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 hover:text-gray-700 transition-colors"
            >
              <Settings className="h-5 w-5" />
            </Link>

            {/* Mobile menu button */}
            <button
              className="md:hidden p-2 rounded-lg text-gray-500 hover:bg-gray-100"
              onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
            >
              <Menu className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Mobile Navigation */}
        {isMobileMenuOpen && (
          <nav className="md:hidden py-4 border-t border-gray-100">
            {navItems.map((item) => (
              <Link
                key={item.path}
                to={item.path}
                className={`block px-4 py-2 rounded-lg text-sm font-medium ${
                  location.pathname === item.path
                    ? 'bg-primary-50 text-primary-600'
                    : 'text-gray-600 hover:bg-gray-100'
                }`}
                onClick={() => setIsMobileMenuOpen(false)}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        )}
      </div>
    </header>
  );
}

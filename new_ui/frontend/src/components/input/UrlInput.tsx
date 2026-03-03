import { useState } from 'react';
import { Link2, Check, X, Loader2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface UrlInputProps {
  onSubmit: (url: string) => void;
  placeholder?: string;
  isLoading?: boolean;
  disabled?: boolean;
}

export default function UrlInput({
  onSubmit,
  placeholder = 'https://arxiv.org/abs/...',
  isLoading = false,
  disabled = false,
}: UrlInputProps) {
  const [url, setUrl] = useState('');
  const [isValid, setIsValid] = useState<boolean | null>(null);

  const validateUrl = (value: string) => {
    try {
      new URL(value);
      return true;
    } catch {
      return false;
    }
  };

  const handleChange = (value: string) => {
    setUrl(value);
    if (value.trim()) {
      setIsValid(validateUrl(value));
    } else {
      setIsValid(null);
    }
  };

  const handleSubmit = () => {
    if (url.trim() && isValid) {
      onSubmit(url.trim());
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="w-full"
    >
      <div className="relative">
        <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
          <Link2 className="h-5 w-5 text-gray-400" />
        </div>
        <input
          type="url"
          value={url}
          onChange={(e) => handleChange(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
          placeholder={placeholder}
          disabled={isLoading || disabled}
          className={`w-full pl-10 pr-24 py-3 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 transition-colors disabled:opacity-60 disabled:cursor-not-allowed ${
            isValid === false
              ? 'border-red-300 bg-red-50'
              : isValid === true
              ? 'border-green-300 bg-green-50'
              : 'border-gray-200 bg-white'
          }`}
        />
        <div className="absolute inset-y-0 right-0 flex items-center pr-2">
          <AnimatePresence mode="wait">
            {isValid !== null && (
              <motion.span
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.8 }}
                className="mr-2"
              >
                {isValid ? (
                  <Check className="h-4 w-4 text-green-500" />
                ) : (
                  <X className="h-4 w-4 text-red-500" />
                )}
              </motion.span>
            )}
          </AnimatePresence>
          <button
            onClick={handleSubmit}
            disabled={!isValid || isLoading || disabled}
            className="px-3 py-1.5 text-sm font-medium text-white bg-primary-600 rounded-md hover:bg-primary-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              'Load'
            )}
          </button>
        </div>
      </div>
      {isValid === false && url.trim() && (
        <p className="mt-1.5 text-xs text-red-500">Please enter a valid URL</p>
      )}
      <p className="mt-2 text-xs text-gray-400">
        Supported: ArXiv, GitHub, and direct PDF links
      </p>
    </motion.div>
  );
}

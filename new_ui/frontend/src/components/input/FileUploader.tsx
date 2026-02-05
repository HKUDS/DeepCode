import { useCallback, useState } from 'react';
import { Upload, File, X, Loader2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { filesApi } from '../../services/api';
import { toast } from '../common/Toaster';

interface FileUploaderProps {
  onFileUploaded: (fileId: string, path: string) => void;
  acceptedTypes?: string[];
  maxSize?: number; // in bytes
  disabled?: boolean;
}

export default function FileUploader({
  onFileUploaded,
  acceptedTypes = ['.pdf', '.md', '.txt'],
  maxSize = 100 * 1024 * 1024, // 100MB
  disabled = false,
}: FileUploaderProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<{
    id: string;
    name: string;
    size: number;
  } | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const uploadFile = async (file: File) => {
    // Validate file type
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!acceptedTypes.includes(ext)) {
      toast.error(
        'Invalid file type',
        `Accepted types: ${acceptedTypes.join(', ')}`
      );
      return;
    }

    // Validate file size
    if (file.size > maxSize) {
      toast.error(
        'File too large',
        `Maximum size: ${Math.round(maxSize / (1024 * 1024))}MB`
      );
      return;
    }

    setIsUploading(true);
    try {
      const result = await filesApi.upload(file);
      setUploadedFile({
        id: result.file_id,
        name: result.filename,
        size: result.size,
      });
      onFileUploaded(result.file_id, result.path);
      toast.success('File uploaded', result.filename);
    } catch (error) {
      toast.error('Upload failed', 'Please try again');
      console.error('Upload error:', error);
    } finally {
      setIsUploading(false);
    }
  };

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);

      const file = e.dataTransfer.files[0];
      if (file) {
        uploadFile(file);
      }
    },
    [uploadFile]
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        uploadFile(file);
      }
    },
    [uploadFile]
  );

  const removeFile = async () => {
    if (uploadedFile) {
      try {
        await filesApi.delete(uploadedFile.id);
      } catch {
        // Ignore delete errors
      }
      setUploadedFile(null);
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };

  return (
    <div className="w-full">
      <AnimatePresence mode="wait">
        {uploadedFile ? (
          <motion.div
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.95 }}
            className="flex items-center justify-between p-4 bg-gray-50 border border-gray-200 rounded-lg"
          >
            <div className="flex items-center space-x-3">
              <div className="p-2 bg-primary-100 rounded-lg">
                <File className="h-5 w-5 text-primary-600" />
              </div>
              <div>
                <p className="font-medium text-sm text-gray-900">
                  {uploadedFile.name}
                </p>
                <p className="text-xs text-gray-500">
                  {formatFileSize(uploadedFile.size)}
                </p>
              </div>
            </div>
            <button
              onClick={removeFile}
              className="p-1.5 text-gray-400 hover:text-gray-600 hover:bg-gray-200 rounded-lg transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
          </motion.div>
        ) : (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onDragOver={disabled ? undefined : handleDragOver}
            onDragLeave={disabled ? undefined : handleDragLeave}
            onDrop={disabled ? undefined : handleDrop}
            className={`relative border-2 border-dashed rounded-lg p-8 text-center transition-colors ${
              disabled
                ? 'border-gray-200 bg-gray-50 opacity-60 cursor-not-allowed'
                : isDragging
                ? 'border-primary-500 bg-primary-50'
                : 'border-gray-300 hover:border-gray-400'
            }`}
          >
            <input
              type="file"
              accept={acceptedTypes.join(',')}
              onChange={handleFileSelect}
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
              disabled={isUploading || disabled}
            />

            {isUploading ? (
              <div className="flex flex-col items-center">
                <Loader2 className="h-10 w-10 text-primary-500 animate-spin mb-3" />
                <p className="text-sm text-gray-600">Uploading...</p>
              </div>
            ) : (
              <div className="flex flex-col items-center">
                <Upload
                  className={`h-10 w-10 mb-3 ${
                    isDragging ? 'text-primary-500' : 'text-gray-400'
                  }`}
                />
                <p className="text-sm font-medium text-gray-700 mb-1">
                  Drop your file here or click to browse
                </p>
                <p className="text-xs text-gray-500">
                  Supports {acceptedTypes.join(', ')} up to{' '}
                  {Math.round(maxSize / (1024 * 1024))}MB
                </p>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

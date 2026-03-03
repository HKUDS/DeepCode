import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, Button } from '../components/common';
import { toast } from '../components/common/Toaster';
import { configApi } from '../services/api';
import { Settings, Server, Cpu, Check } from 'lucide-react';

export default function SettingsPage() {
  const queryClient = useQueryClient();

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: configApi.getSettings,
  });

  const { data: providers } = useQuery({
    queryKey: ['llm-providers'],
    queryFn: configApi.getLLMProviders,
  });

  const updateProviderMutation = useMutation({
    mutationFn: configApi.setLLMProvider,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] });
      toast.success('Settings saved', 'LLM provider updated');
    },
    onError: () => {
      toast.error('Failed to save', 'Please try again');
    },
  });

  const [selectedProvider, setSelectedProvider] = useState('');

  useEffect(() => {
    if (settings?.llm_provider) {
      setSelectedProvider(settings.llm_provider);
    }
  }, [settings]);

  const handleSaveProvider = () => {
    if (selectedProvider && selectedProvider !== settings?.llm_provider) {
      updateProviderMutation.mutate(selectedProvider);
    }
  };

  const providerInfo: Record<string, { name: string; description: string }> = {
    google: {
      name: 'Google Gemini',
      description: 'Uses Gemini models for code generation',
    },
    anthropic: {
      name: 'Anthropic Claude',
      description: 'Uses Claude models for high-quality output',
    },
    openai: {
      name: 'OpenAI',
      description: 'Uses GPT models for code generation',
    },
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-2xl">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-500 mt-1">
          Configure DeepCode to match your preferences
        </p>
      </motion.div>

      {/* LLM Provider */}
      <Card>
        <div className="flex items-center space-x-3 mb-6">
          <div className="p-2 bg-primary-50 rounded-lg">
            <Cpu className="h-5 w-5 text-primary-600" />
          </div>
          <div>
            <h3 className="font-semibold text-gray-900">LLM Provider</h3>
            <p className="text-sm text-gray-500">
              Choose the AI model provider for code generation
            </p>
          </div>
        </div>

        <div className="space-y-3">
          {providers?.available_providers.map((provider) => {
            const info = providerInfo[provider];
            const isSelected = selectedProvider === provider;

            return (
              <button
                key={provider}
                onClick={() => setSelectedProvider(provider)}
                className={`w-full flex items-center justify-between p-4 rounded-lg border-2 transition-colors ${
                  isSelected
                    ? 'border-primary-500 bg-primary-50'
                    : 'border-gray-200 hover:border-gray-300'
                }`}
              >
                <div className="flex items-center space-x-3">
                  <Server
                    className={`h-5 w-5 ${
                      isSelected ? 'text-primary-600' : 'text-gray-400'
                    }`}
                  />
                  <div className="text-left">
                    <div
                      className={`font-medium ${
                        isSelected ? 'text-primary-900' : 'text-gray-900'
                      }`}
                    >
                      {info?.name || provider}
                    </div>
                    <div
                      className={`text-sm ${
                        isSelected ? 'text-primary-600' : 'text-gray-500'
                      }`}
                    >
                      {info?.description || ''}
                    </div>
                  </div>
                </div>
                {isSelected && (
                  <Check className="h-5 w-5 text-primary-600" />
                )}
              </button>
            );
          })}
        </div>

        {selectedProvider !== settings?.llm_provider && (
          <div className="mt-4 pt-4 border-t border-gray-100">
            <Button
              onClick={handleSaveProvider}
              isLoading={updateProviderMutation.isPending}
            >
              Save Changes
            </Button>
          </div>
        )}
      </Card>

      {/* Current Models */}
      <Card>
        <div className="flex items-center space-x-3 mb-4">
          <div className="p-2 bg-gray-100 rounded-lg">
            <Settings className="h-5 w-5 text-gray-600" />
          </div>
          <h3 className="font-semibold text-gray-900">Current Configuration</h3>
        </div>

        <div className="space-y-3">
          <div className="flex justify-between py-2 border-b border-gray-100">
            <span className="text-sm text-gray-500">Active Provider</span>
            <span className="text-sm font-medium text-gray-900">
              {providerInfo[settings?.llm_provider || '']?.name || settings?.llm_provider}
            </span>
          </div>
          <div className="flex justify-between py-2 border-b border-gray-100">
            <span className="text-sm text-gray-500">Planning Model</span>
            <span className="text-sm font-mono text-gray-900">
              {settings?.models?.planning || 'N/A'}
            </span>
          </div>
          <div className="flex justify-between py-2 border-b border-gray-100">
            <span className="text-sm text-gray-500">Implementation Model</span>
            <span className="text-sm font-mono text-gray-900">
              {settings?.models?.implementation || 'N/A'}
            </span>
          </div>
          <div className="flex justify-between py-2">
            <span className="text-sm text-gray-500">Code Indexing</span>
            <span className="text-sm text-gray-900">
              {settings?.indexing_enabled ? 'Enabled' : 'Disabled'}
            </span>
          </div>
        </div>
      </Card>
    </div>
  );
}

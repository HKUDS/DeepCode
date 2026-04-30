import { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, Button } from '../components/common';
import { toast } from '../components/common/Toaster';
import { configApi } from '../services/api';
import { Settings, Server, Cpu, Check } from 'lucide-react';
import type { OpenRouterModelInfo } from '../types/api';

export default function SettingsPage() {
  const queryClient = useQueryClient();
  const [selectedProvider, setSelectedProvider] = useState('');
  const [modelSearch, setModelSearch] = useState('');
  const [selectedModels, setSelectedModels] = useState({
    default: '',
    planning: '',
    implementation: '',
  });

  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: configApi.getSettings,
  });

  const { data: providers } = useQuery({
    queryKey: ['llm-providers'],
    queryFn: configApi.getLLMProviders,
  });

  const { data: openRouterModels, isLoading: isLoadingOpenRouterModels } = useQuery({
    queryKey: ['openrouter-models'],
    queryFn: () => configApi.getOpenRouterModels(),
    enabled: selectedProvider === 'openrouter',
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

  const updateModelsMutation = useMutation({
    mutationFn: configApi.setLLMModels,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] });
      queryClient.invalidateQueries({ queryKey: ['llm-providers'] });
      toast.success('Settings saved', 'New workflows will use the selected models');
    },
    onError: () => {
      toast.error('Failed to save models', 'Please verify the selected model ids');
    },
  });

  useEffect(() => {
    if (settings?.llm_provider) {
      setSelectedProvider(settings.llm_provider);
    }
    if (settings?.models) {
      setSelectedModels({
        default: settings.models.default || settings.models.planning || '',
        planning: settings.models.planning || settings.models.default || '',
        implementation:
          settings.models.implementation || settings.models.default || '',
      });
    }
  }, [settings]);

  const handleSaveProvider = () => {
    if (selectedProvider === 'openrouter') {
      updateModelsMutation.mutate({
        provider: 'openrouter',
        default_model: selectedModels.default,
        planning_model: selectedModels.planning,
        implementation_model: selectedModels.implementation,
      });
      return;
    }
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
    openrouter: {
      name: 'OpenRouter',
      description: 'Route through OpenRouter models such as z-ai/glm-5.1',
    },
  };

  const modelOptions = openRouterModels?.models || [];
  const normalizedSearch = modelSearch.trim().toLowerCase();
  const filteredModels = modelOptions
    .filter((model) => {
      if (!normalizedSearch) return true;
      return (
        model.id.toLowerCase().includes(normalizedSearch) ||
        model.name.toLowerCase().includes(normalizedSearch)
      );
    })
    .slice(0, 200);
  const selectedProviderChanged = selectedProvider !== settings?.llm_provider;
  const selectedModelsChanged =
    selectedModels.default !== (settings?.models?.default || '') ||
    selectedModels.planning !== (settings?.models?.planning || '') ||
    selectedModels.implementation !== (settings?.models?.implementation || '');
  const shouldShowSave =
    selectedProvider === 'openrouter'
      ? selectedProviderChanged || selectedModelsChanged
      : selectedProviderChanged;

  const formatContext = (model?: OpenRouterModelInfo) => {
    if (!model?.context_length) return 'context n/a';
    if (model.context_length >= 1000000) {
      return `${Math.round(model.context_length / 1000000)}M ctx`;
    }
    return `${Math.round(model.context_length / 1000)}K ctx`;
  };

  const formatPrice = (model?: OpenRouterModelInfo) => {
    const prompt = model?.pricing?.prompt;
    const completion = model?.pricing?.completion;
    if (typeof prompt !== 'string' || typeof completion !== 'string') {
      return 'price n/a';
    }
    return `in ${prompt} / out ${completion}`;
  };

  const findModel = (id: string) => modelOptions.find((model) => model.id === id);

  const renderModelSelect = (
    label: string,
    phase: 'default' | 'planning' | 'implementation'
  ) => {
    const selected = findModel(selectedModels[phase]);
    return (
      <div>
        <label className="mb-1 block text-sm font-medium text-gray-700">
          {label}
        </label>
        <select
          value={selectedModels[phase]}
          onChange={(event) =>
            setSelectedModels((current) => ({
              ...current,
              [phase]: event.target.value,
            }))
          }
          className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-100"
        >
          {selectedModels[phase] && !filteredModels.some((m) => m.id === selectedModels[phase]) && (
            <option value={selectedModels[phase]}>{selectedModels[phase]}</option>
          )}
          {filteredModels.map((model) => (
            <option key={`${phase}-${model.id}`} value={model.id}>
              {model.name} · {model.id}
            </option>
          ))}
        </select>
        <div className="mt-1 flex flex-wrap gap-1 text-xs text-gray-500">
          <span>{formatContext(selected)}</span>
          <span>·</span>
          <span>
            {selected?.supported_parameters.includes('tools')
              ? 'tools'
              : 'no tools flag'}
          </span>
          <span>·</span>
          <span>{formatPrice(selected)}</span>
        </div>
      </div>
    );
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

        {selectedProvider === 'openrouter' && (
          <div className="mt-6 space-y-4 border-t border-gray-100 pt-4">
            <div>
              <h4 className="text-sm font-semibold text-gray-900">
                OpenRouter Models
              </h4>
              <p className="mt-1 text-sm text-gray-500">
                Choose model ids used by each workflow phase. The id is the exact
                value sent to OpenRouter, for example z-ai/glm-5.1.
              </p>
            </div>

            <input
              value={modelSearch}
              onChange={(event) => setModelSearch(event.target.value)}
              placeholder="Search model id or name, e.g. glm"
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-100"
            />

            {isLoadingOpenRouterModels ? (
              <div className="text-sm text-gray-500">Loading OpenRouter models...</div>
            ) : (
              <div className="space-y-4">
                {renderModelSelect('Default Model', 'default')}
                {renderModelSelect('Planning Model', 'planning')}
                {renderModelSelect('Implementation Model', 'implementation')}
              </div>
            )}

            {openRouterModels && (
              <div className="rounded-lg bg-gray-50 px-3 py-2 text-xs text-gray-500">
                Model catalog source: {openRouterModels.source}
                {openRouterModels.stale ? ' (stale cache)' : ''} · Showing{' '}
                {filteredModels.length} of {modelOptions.length} models
              </div>
            )}
          </div>
        )}

        {shouldShowSave && (
          <div className="mt-4 pt-4 border-t border-gray-100">
            <Button
              onClick={handleSaveProvider}
              isLoading={
                updateProviderMutation.isPending || updateModelsMutation.isPending
              }
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

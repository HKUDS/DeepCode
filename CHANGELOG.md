# Changelog

All notable changes to DeepCode will be documented in this file.

## [1.0.6-jm] - 2025-10-19

### Added
- **Dynamic Model Limit Detection**: New `utils/model_limits.py` module that automatically detects and adapts to any LLM model's token limits and pricing
- **Loop Detection System**: `utils/loop_detector.py` prevents infinite loops by detecting repeated tool calls, timeouts, and progress stalls
- **Progress Tracking**: 8-phase progress tracking (5% → 100%) with file-level progress indicators in both UI and terminal
- **Abort Mechanism**: "Stop Processing" button in UI with global abort flag for clean process termination
- **Cache Cleanup Scripts**: `start_clean.bat` and `start_clean.ps1` to clear Python cache before starting
- **Enhanced Error Display**: Real-time error messages in both UI and terminal with timestamps
- **File Progress Tracking**: Shows files completed/total with estimated time remaining

### Fixed
- **Unique Folder Naming**: Each project run now creates `paper_{timestamp}` folders instead of reusing `pdf_output`
- **PDF Save Location**: PDFs now save to `deepcode_lab/papers/` instead of system temp directory
- **Duplicate Folder Prevention**: System detects and reuses existing `paper_{timestamp}` folders instead of creating duplicates
- **Token Limit Compliance**: Fixed `max_tokens` to respect model limits (e.g., gpt-4o-mini's 16,384 token limit)
- **Empty Plan Detection**: System now fails early with clear error messages when initial plan is empty or invalid
- **Process Hanging**: Fixed infinite loops and hanging on errors - process now exits cleanly
- **Token Cost Tracking**: Restored accurate token usage and cost display (was showing $0.0000)
- **PDF to Markdown Conversion**: Fixed automatic conversion and file location handling
- **Document Segmentation**: Properly uses configured 50K character threshold from `mcp_agent.config.yaml`

### Changed
- **Model-Aware Token Management**: Token limits now adapt automatically based on configured model instead of hardcoded values
- **Cost Calculation**: Dynamic pricing based on actual model rates (OpenAI, Anthropic)
- **Retry Logic**: Token limits for retries now respect model maximum (87.5% → 95% → 98% of max)
- **Segmentation Workflow**: Better integration with code implementation phase
- **Error Handling**: Enhanced error propagation - errors no longer reported as "success"
- **UI Display**: Shows project folder name after PDF conversion for better visibility
- **Terminal Logging**: Added timestamps to all progress messages

### Technical Improvements
- Added document-segmentation server to code implementation workflow for better token management
- Improved error handling in agent orchestration engine with proper cleanup
- Enhanced subprocess handling on Windows (hide console windows, prevent hanging)
- Better LibreOffice detection on Windows using direct path checking
- Fixed input data format consistency (JSON with `paper_path` key)
- Added comprehensive logging throughout the pipeline
- Improved resource cleanup on errors and process termination

### Documentation
- Translated Chinese comments to English in core workflow files
- Added inline documentation for new utility modules
- Created startup scripts with clear usage instructions

### Breaking Changes
- None - all changes are backward compatible

### Known Issues
- Some Chinese comments remain in non-critical files (cli, tools) - translation in progress
- tiktoken package optional warning (doesn't affect functionality)

---

## [1.0.5] - Previous Release

See previous releases for earlier changes.


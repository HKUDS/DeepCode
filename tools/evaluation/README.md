# Code Evaluation Tools - Refactored Structure

This directory contains the refactored code evaluation tools, split from the original monolithic `code_evaluation_server.py` (210KB) into specialized modules.

## File Size Comparison

- **Original**: `code_evaluation_server_original_backup.py` (210KB)
- **Current**: `code_evaluation_server.py` (35KB) - Minimal version with essential tools
- **Modular**: 6 specialized modules (total ~190KB, but organized)

## Module Structure

### 1. `core_evaluation_server.py` (38KB)
**Purpose**: Basic repository analysis and evaluation
**Tools**:
- `analyze_repo_structure` - Repository structure analysis
- `detect_dependencies` - Dependency detection across languages
- `assess_code_quality` - Code quality metrics
- `evaluate_documentation` - Documentation completeness
- `check_reproduction_readiness` - Reproduction readiness assessment
- `generate_evaluation_summary` - Comprehensive evaluation summary

**Used by**: `analyzer_agent.py`

### 2. `lsp_tools_server.py` (41KB)
**Purpose**: LSP (Language Server Protocol) enhanced analysis
**Tools**:
- `setup_lsp_servers` - Initialize LSP servers for detected languages
- `lsp_find_symbol_references` - Find symbol references using LSP
- `lsp_get_diagnostics` - Get LSP diagnostics for files
- `lsp_get_code_actions` - Get LSP code actions for ranges
- `lsp_generate_code_fixes` - Generate LSP-based code fixes
- `lsp_apply_workspace_edit` - Apply LSP workspace edits

**Used by**: `revision_agent.py`, `analyzer_agent.py`

### 3. `static_analysis_server.py` (35KB)
**Purpose**: Static analysis and code formatting
**Tools**:
- `perform_static_analysis` - Comprehensive static analysis
- `auto_fix_formatting` - Automatic code formatting
- `generate_static_issues_report` - Structured issues report

**Used by**: `analyzer_agent.py`

### 4. `error_analysis_server.py` (40KB)
**Purpose**: Error analysis and remediation
**Tools**:
- `parse_error_traceback` - Parse Python tracebacks
- `analyze_import_dependencies` - Import dependency analysis
- `generate_error_analysis_report` - Comprehensive error analysis
- `generate_precise_code_fixes` - Generate targeted fixes
- `apply_code_fixes_with_diff` - Apply fixes with diff generation

**Used by**: `revision_agent.py`, `analyzer_agent.py`

### 5. `revision_tools_server.py` (26KB)
**Purpose**: Code revision and completeness assessment
**Tools**:
- `detect_empty_files` - Find empty/minimal files needing implementation
- `detect_missing_files` - Find missing essential files
- `generate_code_revision_report` - Comprehensive revision report

**Used by**: `analyzer_agent.py`

### 6. `sandbox_tools_server.py` (9.6KB)
**Purpose**: Sandbox execution and validation
**Tools**:
- `execute_in_sandbox` - Execute commands in isolated environment
- `run_code_validation` - Run validation tests

**Used by**: `analyzer_agent.py`

## Tool Usage Analysis

Based on analysis of the workflow agents, the following tools are actually used:

### By `analyzer_agent.py`:
- `analyze_repo_structure`
- `detect_dependencies` 
- `assess_code_quality`
- `evaluate_documentation`
- `check_reproduction_readiness`
- `generate_evaluation_summary`
- `detect_empty_files`
- `detect_missing_files`
- `generate_code_revision_report`
- `setup_lsp_servers`
- `auto_fix_formatting`
- `lsp_get_diagnostics`
- `lsp_generate_code_fixes`
- `lsp_apply_workspace_edit`
- `perform_static_analysis`
- `run_code_validation`
- `generate_error_analysis_report`
- `analyze_import_dependencies`

### By `revision_agent.py`:
- `setup_lsp_servers`
- `generate_error_analysis_report`
- `lsp_get_diagnostics`
- `lsp_generate_code_fixes`
- `lsp_apply_workspace_edit`
- `generate_precise_code_fixes`
- `apply_code_fixes_with_diff`

### Tools NOT used (removed from original):
- Various language-specific formatting functions
- Complex LSP client implementation details
- Unused helper functions and data classes
- Redundant analysis tools
- Deprecated or experimental features

## Benefits of Refactoring

1. **Maintainability**: Each module has a clear purpose and can be maintained independently
2. **Modularity**: Tools can be imported individually or as groups
3. **Reduced Complexity**: Main server file is now 35KB vs 210KB original
4. **Better Organization**: Related tools are grouped together
5. **Easier Testing**: Each module can be tested independently
6. **Cleaner Dependencies**: Clear separation of concerns

## Usage

### Option 1: Use the minimal version (current default)
```python
# Uses code_evaluation_server.py (35KB)
# Contains only essential tools with simplified implementations
```

### Option 2: Use the modular version
```python
# Import specific modules as needed
from evaluation.core_evaluation_server import analyze_repo_structure
from evaluation.lsp_tools_server import setup_lsp_servers
```

### Option 3: Use the combined refactored version
```python
# Uses code_evaluation_server_refactored.py
# Imports and combines all modules
```

## Migration Notes

- Original file backed up as `code_evaluation_server_original_backup.py`
- Current implementation maintains API compatibility
- All tools used by workflow agents are preserved
- Unused tools and helper functions removed
- Simplified implementations for better reliability

## Future Improvements

1. Add proper LSP client implementations when MCP dependencies are available
2. Enhance error analysis with more sophisticated pattern matching
3. Add more language-specific static analysis tools
4. Improve sandbox isolation and security
5. Add comprehensive test coverage for all modules

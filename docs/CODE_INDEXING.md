# Code Indexing Guide for DeepCode

## Overview

DeepCode의 Code Indexing 시스템은 참조 코드베이스를 분석하여 Knowledge Graph 기반의 인덱스를 생성합니다.
이 인덱스는 코드 생성 시 관련 코드 패턴, 함수, 개념을 검색하고 참조하는 데 사용됩니다.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Code Indexing System                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────┐     ┌─────────────────┐     ┌───────────┐ │
│  │  Reference Code │ --> │  CodeIndexer    │ --> │  JSON     │ │
│  │  (Git Repos)    │     │  (LLM Analysis) │     │  Indexes  │ │
│  └─────────────────┘     └─────────────────┘     └───────────┘ │
│         ↑                                              ↓       │
│  deepcode_lab/                                 deepcode_lab/   │
│  reference_code/                               indexes/        │
│                                                                 │
│                    ┌─────────────────────┐                     │
│                    │ code-reference-     │                     │
│                    │ indexer MCP Server  │ <-- Code Generation │
│                    └─────────────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### 1. CodeIndexer (`tools/code_indexer.py`)
- LLM 기반 코드 분석 엔진
- 파일 구조, 함수, 개념, 의존성 추출
- 관계 분석 및 신뢰도 점수 계산

### 2. Reference Code Indexer Script (`tools/run_reference_indexer.py`)
- 독립 실행 스크립트
- `reference_code/` 디렉토리 전체 인덱싱

### 3. Code Reference Indexer MCP Server (`tools/code_reference_indexer.py`)
- 인덱스 검색 MCP 서버
- `search_code_references()` - 관련 코드 검색
- `get_indexes_overview()` - 인덱스 개요 조회

---

## LLM Indexing Process (상세)

CodeIndexer는 4단계의 LLM 호출을 통해 인덱스를 생성합니다.

### 전체 플로우

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         CodeIndexer LLM Pipeline                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  Reference Code          Target Structure                                       │
│  (deepcode_lab/          (tree 형태의                                           │
│   reference_code/)        프로젝트 구조)                                         │
│         │                       │                                               │
│         └───────────┬───────────┘                                               │
│                     ↓                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Step 1: Directory Filtering (대용량 레포 전용)                          │   │
│  │ "어떤 디렉토리가 target과 관련있나?"                                     │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                     ↓                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Step 2: File Pre-filtering                                              │   │
│  │ "어떤 파일이 target 구현에 도움이 될까?"                                 │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                     ↓                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Step 3: File Analysis (파일별 반복)                                     │   │
│  │ "이 파일의 함수, 개념, 의존성은?"                                        │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                     ↓                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ Step 4: Relationship Analysis (파일별 반복)                             │   │
│  │ "이 파일이 target의 어떤 파일 구현에 도움이 될까?"                       │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                     ↓                                                           │
│              JSON Index 생성                                                    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

### Step 1: Directory Filtering (대용량 레포 전용)

**함수**: `_filter_directories_first()`

**목적**: 파일 트리가 50KB 초과 시 먼저 관련 디렉토리만 식별

**프롬프트**:
```
You are a code analysis expert. Analyze this repository's DIRECTORY
structure and identify which directories are most likely to contain
code relevant to the target project.

Target Project Structure:
{target_structure}

Repository Directory Structure:
{dir_tree}

Return ONLY a JSON object with the most relevant directories (max 10):
{
    "relevant_directories": ["dir1", "dir2/subdir", "dir3"],
    "reasoning": "brief explanation"
}
```

**LLM 응답 예시**:
```json
{
    "relevant_directories": ["src/models", "src/utils", "core"],
    "reasoning": "These directories contain ML model implementations and utilities"
}
```

---

### Step 2: File Pre-filtering

**함수**: `pre_filter_files()`

**목적**: 파일 트리에서 target 구현에 관련있는 파일만 선별

**프롬프트**:
```
You are a code analysis expert. Please analyze the following code
repository file tree based on the target project structure and filter
out files that may be relevant to the target project.

Target Project Structure:
{target_structure}

Code Repository File Tree:
{file_tree}

Please analyze which files might be helpful for implementing the
target project structure, including:
- Core algorithm implementation files (GCN, recommendation systems, etc.)
- Data processing and preprocessing files
- Loss functions and evaluation metric files
- Configuration and utility files
- Test files
- Documentation files

Please return the filtering results in JSON format:
{
    "relevant_files": [
        {
            "file_path": "file path relative to repository root",
            "relevance_reason": "why this file is relevant",
            "confidence": 0.0-1.0,
            "expected_contribution": "expected contribution to target"
        }
    ],
    "summary": {
        "total_files_analyzed": "...",
        "relevant_files_count": "...",
        "filtering_strategy": "explanation of filtering strategy"
    }
}

Only return files with confidence > 0.3.
```

**LLM 응답 예시**:
```json
{
    "relevant_files": [
        {
            "file_path": "src/models/gcn.py",
            "relevance_reason": "GCN implementation matching target architecture",
            "confidence": 0.9,
            "expected_contribution": "Core GCN encoder implementation"
        },
        {
            "file_path": "src/utils/metrics.py",
            "relevance_reason": "Evaluation metrics for recommendation",
            "confidence": 0.7,
            "expected_contribution": "NDCG, Recall metric implementations"
        }
    ],
    "summary": {
        "total_files_analyzed": "150",
        "relevant_files_count": "12",
        "filtering_strategy": "Selected files implementing ML models and utilities"
    }
}
```

---

### Step 3: File Analysis

**함수**: `analyze_file()`

**목적**: 각 파일의 내용을 분석하여 구조화된 요약 생성

**프롬프트**:
```
Analyze this code file and provide a structured summary:

File: {file_name}
Content:
```
{file_content}
```

Please provide analysis in this JSON format:
{
    "file_type": "description of what type of file this is",
    "main_functions": ["list", "of", "main", "functions", "or", "classes"],
    "key_concepts": ["important", "concepts", "algorithms", "patterns"],
    "dependencies": ["external", "libraries", "or", "imports"],
    "summary": "2-3 sentence summary of what this file does"
}

Focus on the core functionality and potential reusability.
```

**LLM 응답 예시**:
```json
{
    "file_type": "Python module - Graph Convolutional Network implementation",
    "main_functions": ["GCNLayer", "GCNEncoder", "forward", "aggregate"],
    "key_concepts": ["graph convolution", "message passing", "node embedding"],
    "dependencies": ["torch", "torch_geometric", "numpy"],
    "summary": "Implements a Graph Convolutional Network encoder with customizable layers. Supports various aggregation methods and includes dropout regularization."
}
```

---

### Step 4: Relationship Analysis

**함수**: `find_relationships()`

**목적**: 분석된 파일과 target 구조 간의 관계 매핑

**프롬프트**:
```
Analyze the relationship between this existing code file and the
target project structure.

Existing File Analysis:
- Path: {file_path}
- Type: {file_type}
- Functions: {main_functions}
- Concepts: {key_concepts}
- Summary: {file_summary}

Target Project Structure:
{target_structure}

Available relationship types (with priority weights):
- direct_match (1.0): Direct implementation match
- partial_match (0.8): Partial functionality match
- reference (0.6): Reference or utility function
- utility (0.4): General utility or helper

Identify potential relationships and provide analysis in this JSON format:
{
    "relationships": [
        {
            "target_file_path": "path/in/target/structure",
            "relationship_type": "direct_match|partial_match|reference|utility",
            "confidence_score": 0.0-1.0,
            "helpful_aspects": ["specific", "aspects", "that", "help"],
            "potential_contributions": ["how", "this", "contributes"],
            "usage_suggestions": "detailed suggestion on how to use this file"
        }
    ]
}
```

**LLM 응답 예시**:
```json
{
    "relationships": [
        {
            "target_file_path": "src/core/gcn.py",
            "relationship_type": "direct_match",
            "confidence_score": 0.92,
            "helpful_aspects": ["GCN architecture", "layer implementation", "aggregation"],
            "potential_contributions": ["Core encoder structure", "Message passing logic"],
            "usage_suggestions": "Use as primary reference for implementing GCN encoder. Adapt layer configuration for target requirements."
        },
        {
            "target_file_path": "src/models/encoder.py",
            "relationship_type": "partial_match",
            "confidence_score": 0.65,
            "helpful_aspects": ["embedding generation", "forward pass"],
            "potential_contributions": ["Embedding layer patterns"],
            "usage_suggestions": "Reference for embedding layer design patterns."
        }
    ]
}
```

---

### Relationship Types (관계 유형)

| 유형 | 가중치 | 설명 | 예시 |
|------|--------|------|------|
| `direct_match` | 1.0 | 직접적인 구현 매칭 | GCN 파일 → target GCN |
| `partial_match` | 0.8 | 부분적 기능 매칭 | 일부 함수만 관련 |
| `reference` | 0.6 | 참조용 코드 | 비슷한 패턴의 다른 모델 |
| `utility` | 0.4 | 범용 유틸리티 | 공통 헬퍼 함수 |

---

### LLM 호출 횟수 계산

```
Total LLM Calls =
    1 (directory filtering, if large repo)
  + 1 (file pre-filtering)
  + N (file analysis, N = filtered file count)
  + N (relationship analysis)

예: 파일 100개 → 약 202회 LLM 호출
예: 파일 500개 → 약 1,002회 LLM 호출
```

**비용 절감 팁**:
- `--mock` 옵션으로 먼저 테스트
- 불필요한 파일 사전 정리
- `min_confidence_score` 높이기 (기본 0.3)

---

## Quick Start

### Step 1: Clone Reference Repositories

```bash
# 참조할 코드베이스를 clone
cd deepcode_lab/reference_code/
git clone https://github.com/example/reference-project-1.git
git clone https://github.com/example/reference-project-2.git
```

### Step 2: Run Indexing

```bash
# 기본 실행
python tools/run_reference_indexer.py

# 상세 로그 출력
python tools/run_reference_indexer.py --verbose

# 커스텀 경로 지정
python tools/run_reference_indexer.py \
  --reference-path /path/to/code \
  --output-path /path/to/indexes

# 테스트 모드 (LLM 호출 없이)
python tools/run_reference_indexer.py --mock --verbose
```

### Step 3: Enable Indexing in CLI

```bash
# 인덱싱 활성화하여 CLI 실행
python cli/main_cli.py --enable-indexing

# 또는 대화형 메뉴에서 [C] Configure 선택 후 Toggle
```

## Index File Structure

생성되는 JSON 인덱스 파일 구조:

```json
{
  "repo_name": "example-project",
  "total_files": 42,
  "file_summaries": [
    {
      "file_path": "src/core/main.py",
      "file_type": "Python module - Core functionality",
      "main_functions": ["process_data", "run_pipeline"],
      "key_concepts": ["data processing", "pipeline"],
      "dependencies": ["numpy", "pandas"],
      "summary": "Main entry point for data processing pipeline.",
      "lines_of_code": 250,
      "last_modified": "2024-01-15T10:30:00"
    }
  ],
  "relationships": [
    {
      "repo_file_path": "src/core/main.py",
      "target_file_path": "src/pipeline.py",
      "relationship_type": "direct_match",
      "confidence_score": 0.85,
      "helpful_aspects": ["pipeline architecture", "data flow"],
      "potential_contributions": ["core implementation pattern"],
      "usage_suggestions": "Reference for implementing data pipeline"
    }
  ],
  "analysis_metadata": {
    "analysis_date": "2024-01-15T12:00:00",
    "analyzer_version": "1.4.0",
    "files_before_filtering": 100,
    "files_after_filtering": 42,
    "filtering_efficiency": 58.0
  }
}
```

## Configuration

### indexer_config.yaml

```yaml
# LLM 설정
llm:
  model_provider: "openai"  # or "anthropic"
  max_tokens: 4000
  temperature: 0.3
  request_delay: 0.5  # API 요청 간격 (초)

# 파일 분석 설정
file_analysis:
  max_file_size: 1048576  # 1MB
  max_content_length: 3000
  supported_extensions:
    - ".py"
    - ".js"
    - ".ts"
    # ... more extensions
  skip_directories:
    - "__pycache__"
    - "node_modules"
    - ".git"

# 관계 분석 설정
relationships:
  min_confidence_score: 0.3
  high_confidence_threshold: 0.7

# 성능 설정
performance:
  enable_concurrent_analysis: false  # API 제한 회피
  enable_content_caching: true
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DEEPCODE_REFERENCE_PATH` | 참조 코드 경로 | `deepcode_lab/reference_code` |
| `DEEPCODE_INDEXES_PATH` | 인덱스 출력 경로 | `deepcode_lab/indexes` |

## Troubleshooting

### 인덱싱이 실행되지 않음
1. `reference_code/` 디렉토리에 레포지토리가 있는지 확인
2. API 키 설정 확인 (`mcp_agent.secrets.yaml`)

### 인덱스 검색 결과가 없음
1. 인덱스 파일이 `deepcode_lab/indexes/`에 생성되었는지 확인
2. CLI에서 `--enable-indexing` 옵션 사용

### LLM API 오류
1. API 키 유효성 확인
2. 요청 간격 늘리기 (`request_delay: 1.0`)
3. `--mock` 옵션으로 테스트

---

### 🔥 대용량 레포지토리 인덱싱 오류

#### 증상
```
Error code: 400 - max_tokens must be at least 1, got -746049
```

#### 원인
파일이 너무 많은 레포지토리(예: 20,000+ 파일)를 인덱싱할 때, 전체 파일 트리를 LLM 프롬프트에 넣으면 토큰이 초과되어 `max_tokens`가 음수가 됨.

```
파일 트리 844,633자 ≈ 200,000+ 토큰
→ max_tokens = context_limit - input_tokens = 음수
```

#### 해결 방법

**방법 1: 불필요한 파일 정리 (권장)**

```bash
# 삭제될 파일 수 확인
find deepcode_lab/reference_code/your_repo -type f \
  ! -name "*.py" ! -name "*.md" ! -name "*.txt" \
  ! -name "*.yaml" ! -name "*.yml" ! -name "*.json" \
  | wc -l

# 코드 파일만 남기고 삭제 (이미지, 바이너리 등 제거)
find deepcode_lab/reference_code/your_repo -type f \
  ! -name "*.py" ! -name "*.md" ! -name "*.txt" \
  ! -name "*.yaml" ! -name "*.yml" ! -name "*.json" \
  -delete

# 빈 폴더 정리
find deepcode_lab/reference_code/your_repo -type d -empty -delete

# 결과 확인
find deepcode_lab/reference_code/your_repo -type f | wc -l
```

**방법 2: 특정 파일 유형만 삭제**

```bash
# Docker 관련 파일 삭제
find deepcode_lab/reference_code -type f \
  \( -name "Dockerfile*" -o -name "*.dockerfile" \
     -o -name "docker-compose*.yml" \) -delete

# 이미지 파일 삭제
find deepcode_lab/reference_code -type f \
  \( -name "*.png" -o -name "*.jpg" -o -name "*.jpeg" \
     -o -name "*.gif" -o -name "*.svg" -o -name "*.ico" \) -delete

# 빌드 산출물 삭제
find deepcode_lab/reference_code -type f \
  \( -name "*.pyc" -o -name "*.pyo" -o -name "*.so" \
     -o -name "*.o" -o -name "*.a" \) -delete
```

**방법 3: 관련 폴더만 인덱싱**

레포지토리 전체가 아닌 관련 있는 하위 폴더만 복사하여 인덱싱:

```bash
# 필요한 폴더만 복사
mkdir -p deepcode_lab/reference_code/my_subset
cp -r original_repo/src deepcode_lab/reference_code/my_subset/
cp -r original_repo/core deepcode_lab/reference_code/my_subset/

# 인덱싱 실행
python tools/run_reference_indexer.py --verbose
```

#### 인덱서 내부 동작

대용량 레포지토리 감지 시 자동으로 2단계 필터링 수행:

```
┌─────────────────────────────────────────────────────────────────┐
│  파일 트리 > 50KB?                                               │
│                                                                 │
│  No → 전체 파일 트리 분석                                        │
│                                                                 │
│  Yes → 2단계 필터링:                                             │
│    ┌─────────────────────────────────────────────────────────┐  │
│    │ Pass 1: 디렉토리 구조만 분석 (depth=2, max 100개)        │  │
│    │   repo/                                                 │  │
│    │   ├── src/ (234 code files)                            │  │
│    │   ├── tests/ (56 code files)                           │  │
│    │   └── lib/ (89 code files)                             │  │
│    │                                                         │  │
│    │ → LLM: "관련 디렉토리는?"                                │  │
│    └─────────────────────────────────────────────────────────┘  │
│                         ↓                                       │
│    ┌─────────────────────────────────────────────────────────┐  │
│    │ Pass 2: 관련 디렉토리의 파일만 분석 (각 50개 제한)        │  │
│    └─────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

#### 레포지토리 크기별 권장사항

| 파일 수 | 권장 조치 |
|---------|----------|
| < 500 | 그대로 인덱싱 |
| 500 ~ 2,000 | 불필요 파일 정리 권장 |
| 2,000 ~ 10,000 | 불필요 파일 정리 필수, 하위 폴더 선택 권장 |
| > 10,000 | 관련 폴더만 별도 추출하여 인덱싱 |

#### LLM이 관련 파일을 찾지 못한 경우

로그 예시:
```
LLM filtering completed: 0 relevant files selected
Filtering strategy: No files were found that implement ML concepts...
LLM filtering failed, will analyze all files
```

이는 **오류가 아님**. target_structure와 reference 코드의 도메인이 다를 때 발생:
- target: ML/추천시스템 구조
- reference: 하드웨어 인터페이스 코드

이 경우 LLM이 관련 파일을 찾지 못하고 전체 파일을 분석합니다.

---

## Best Practices

1. **선별적 인덱싱**: 관련성 높은 레포지토리만 인덱싱
2. **정기 업데이트**: 참조 코드 변경 시 재인덱싱
3. **인덱스 버전 관리**: 인덱스 파일도 버전 관리에 포함
4. **API 비용 관리**: `--mock` 옵션으로 먼저 테스트

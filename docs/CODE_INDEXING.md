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

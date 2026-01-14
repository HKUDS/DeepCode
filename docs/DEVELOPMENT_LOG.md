# DeepCode 사내 환경 적용 - 개발 기록

## 2026-01-14 개발 내용

### 목표
1. Graph-based Code Indexing 구현/활성화
2. CLI 입력창 정상화 (PTY 호환성 문제 해결)

---

## 1. Code Indexing 분석 및 구현

### 분석 결과

#### 기존 구조
- `tools/code_indexer.py`: 실제 인덱싱 생성 (LLM 기반 코드 분석)
- `tools/code_reference_indexer.py`: 이미 생성된 인덱스 파일 검색 (MCP 서버)
- `workflows/codebase_index_workflow.py`: 인덱싱 워크플로우 orchestration

#### 문제점
1. `reference_code/` 디렉토리가 존재하지 않았음
2. `reference_code/`를 직접 인덱싱하는 독립 스크립트가 없었음
3. CLI에서 `enable_indexing = False`가 기본값

### 구현 내용

#### 1.1 디렉토리 구조 생성
```
deepcode_lab/
├── reference_code/   # 인덱싱 대상 코드베이스 (git clone)
└── indexes/          # 생성된 JSON 인덱스 파일
```

#### 1.2 독립 인덱싱 스크립트 생성
**파일:** `tools/run_reference_indexer.py`

```python
# 실행 예시
python tools/run_reference_indexer.py              # 기본 실행
python tools/run_reference_indexer.py --verbose    # 상세 로그
python tools/run_reference_indexer.py --mock       # 테스트 모드
```

**주요 기능:**
- `reference_code/` 내 모든 레포지토리 자동 인덱싱
- 환경 변수를 통한 경로 설정 지원
- Mock 모드로 LLM 호출 없이 테스트 가능
- 상세 로그 및 진행 상황 출력

#### 1.3 CLI 인덱싱 옵션 추가
**파일:** `cli/main_cli.py`

```bash
python cli/main_cli.py --enable-indexing  # 인덱싱 활성화
```

---

## 2. CLI 렌더링 문제 해결

### 분석 결과

#### 원인
- Code Server (VS Code 웹 버전) 터미널의 PTY 에뮬레이션 제한
- ANSI 이스케이프 코드와 `input()` 조합 시 렌더링 문제
- 비-TTY 환경에서의 호환성 이슈

### 구현 내용

#### 2.1 Colors 클래스 개선
**파일:** `cli/cli_interface.py`

```python
class Colors:
    # 환경 변수로 호환성 모드 감지
    _simple_mode = os.environ.get("DEEPCODE_CLI_SIMPLE", "0") == "1" or \
                   os.environ.get("NO_COLOR", "") != "" or \
                   not os.isatty(1)

    # 호환성 모드에서 ANSI 코드 비활성화
    BOLD = "" if _simple_mode else "\033[1m"
    # ... 모든 색상 코드에 적용
```

#### 2.2 심플 메뉴 추가
호환성 모드에서는 ANSI 코드 없이 깔끔한 텍스트 메뉴 표시:

```
========================================
        DeepCode CLI - MAIN MENU
========================================

  [U] Process URL         [F] Upload File
  [T] Chat Input          [R] Requirement Analysis
  [C] Configure           [H] History
  [Q] Quit

----------------------------------------
  Pipeline Mode: OPTIMIZED
  Codebase Indexing: Disabled
  Document Processing: SMART
----------------------------------------
```

#### 2.3 CLI 옵션 추가
**파일:** `cli/main_cli.py`

```bash
python cli/main_cli.py --simple   # 호환성 모드
python cli/main_cli.py --compat   # 동일
```

#### 2.4 환경 변수 지원

| 변수 | 설명 |
|------|------|
| `DEEPCODE_CLI_SIMPLE=1` | 호환성 모드 활성화 |
| `DEEPCODE_NO_COLOR=1` | 색상 비활성화 |
| `NO_COLOR` | 표준 색상 비활성화 |

---

## 3. 변경된 파일 목록

### 신규 생성
- `tools/run_reference_indexer.py` - 독립 인덱싱 스크립트
- `docs/CODE_INDEXING.md` - 인덱싱 가이드
- `docs/CLI_TROUBLESHOOTING.md` - CLI 문제 해결 가이드
- `docs/DEVELOPMENT_LOG.md` - 개발 기록 (본 문서)
- `deepcode_lab/reference_code/` - 참조 코드 디렉토리
- `deepcode_lab/indexes/` - 인덱스 출력 디렉토리

### 수정됨
- `cli/cli_interface.py`
  - Colors 클래스에 호환성 모드 추가
  - CLIInterface에 simple_mode 지원 추가
  - create_menu()에 심플 메뉴 추가
  - get_user_input()에 예외 처리 및 flush 추가

- `cli/main_cli.py`
  - `--simple`, `--compat` 옵션 추가
  - `--enable-indexing` 옵션 추가
  - main()에서 호환성 모드 초기화

---

## 4. 사용 방법

### 코드 인덱싱 활성화

```bash
# 1. 참조 코드 준비
cd deepcode_lab/reference_code/
git clone https://github.com/example/project.git

# 2. 인덱싱 실행
python tools/run_reference_indexer.py --verbose

# 3. 인덱싱 활성화하여 CLI 실행
python cli/main_cli.py --enable-indexing
```

### CLI 호환성 모드

```bash
# Code Server 등 PTY 이슈가 있는 환경에서
python cli/main_cli.py --simple
```

---

## 5. CLI Simple Mode 개선 (2026-01-14 추가)

### 문제점
`--simple` 모드에서도 "Tokens | usage 0 tokens | $0.0000" 라인이 표시되며 터미널 입력을 방해함.

### 원인 분석
1. mcp_agent 라이브러리가 Rich 콘솔을 사용하여 진행률 표시
2. `progress_display: false` 설정만으로는 콘솔 출력이 완전히 비활성화되지 않음
3. 환경변수 방식은 mcp_agent가 YAML 파일에서 설정을 읽기 때문에 작동하지 않음

### 해결 방법
Simple mode 실행 시 `mcp_agent.config.yaml` 파일을 동적으로 수정:

```python
def disable_mcp_console_logger():
    """Simple mode에서 콘솔 로거 비활성화"""
    # 1. 원본 config 백업
    # 2. logger.type을 'file'로 변경
    # 3. logger.transports에서 'console' 제거
    # 4. progress_display: false 설정
```

### 변경 내용

**파일:** `cli/main_cli.py`
- `disable_mcp_console_logger()` 함수 추가
- `restore_mcp_config()` 함수 추가 (종료 시 원본 복원)
- main() 함수에서 simple mode 시 config 수정/복원 로직 추가

### 설정 변경 내용

Simple mode 실행 시 config가 다음과 같이 변경됨:

```yaml
# Before (original)
logger:
  type: console
  transports:
    - console
    - file
  progress_display: false

# After (simple mode)
logger:
  type: file
  transports:
    - file
  progress_display: false
```

---

## 6. 인덱스 기반 코드 생성 통합 (2026-01-14 추가)

### 변경 내용

1. **프롬프트 업데이트** (`prompts/code_prompts.py`)
   - `{INDEXES_PATH}` 플레이스홀더 추가
   - 하드코딩된 경로 (`/home/agent/indexes`) 제거

2. **워크플로우 업데이트** (`workflows/code_implementation_workflow_index.py`)
   - 프로젝트 루트 기준 인덱스 경로 자동 계산
   - 인덱스 파일 존재 여부 확인 및 경고 로그
   - 시스템 프롬프트에 인덱스 경로 자동 대입

### 사용 방법

```bash
# 1. 인덱스 생성 (deepcode_lab/reference_code/에 코드베이스 추가 후)
python tools/run_reference_indexer.py --verbose

# 2. 인덱싱 활성화하여 CLI 실행
python cli/main_cli.py --enable-indexing --simple

# 3. chat 모드에서 코드 생성 요청
# Agent가 자동으로 search_code_references 도구를 사용하여 참조 코드 검색
```

### 인덱스 위치

- **인덱스 파일**: `deepcode_lab/indexes/*.json`
- **참조 코드베이스**: `deepcode_lab/reference_code/`

### 주의사항

인덱스가 없으면 경고가 표시되지만 워크플로우는 계속 실행됩니다:
```
⚠️ Indexes directory exists but is empty: /path/to/deepcode_lab/indexes
   Run 'python tools/run_reference_indexer.py' to create indexes
```

---

## 7. 향후 개선 사항

1. **증분 인덱싱**: 변경된 파일만 재인덱싱
2. **인덱스 캐싱**: 인덱스 로드 시간 단축
3. **실시간 진행률**: 인덱싱 진행 상황 표시 개선
4. **Vector DB 통합**: 대규모 코드베이스 지원

---

## 8. 테스트 방법

### 인덱싱 테스트
```bash
# Mock 모드로 테스트 (LLM 호출 없음)
python tools/run_reference_indexer.py --mock --verbose
```

### CLI 테스트
```bash
# 호환성 모드 테스트 (console 로거 자동 비활성화)
python cli/main_cli.py --simple

# 인덱싱 활성화 테스트
python cli/main_cli.py --enable-indexing --simple

# 환경변수 방식 (config 수정 방식으로 대체됨)
# DEEPCODE_CLI_SIMPLE=1 python cli/main_cli.py
```

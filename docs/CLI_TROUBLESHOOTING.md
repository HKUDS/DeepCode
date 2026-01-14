# CLI Troubleshooting Guide

## 개요

이 문서는 DeepCode CLI 실행 시 발생할 수 있는 문제들과 해결 방법을 설명합니다.

## 일반적인 문제들

### 1. 입력창이 제대로 표시되지 않음

**증상:**
- 프롬프트 입력창이 깜빡임
- 키보드 입력이 화면에 표시되지 않음
- 메뉴가 깨져서 보임

**원인:**
- Code Server, VS Code 웹 터미널 등 PTY 에뮬레이션 제한
- ANSI 이스케이프 코드 호환성 문제

**해결 방법:**

```bash
# 방법 1: 호환성 모드로 실행 (권장)
python cli/main_cli.py --simple

# 방법 2: 환경 변수 설정
export DEEPCODE_CLI_SIMPLE=1
python cli/main_cli.py

# 방법 3: NO_COLOR 표준 사용
export NO_COLOR=1
python cli/main_cli.py
```

### 2. 메뉴 색상이 표시되지 않음

**증상:**
- 터미널에 ANSI 코드가 텍스트로 출력됨 (예: `[91m`)
- 메뉴가 읽기 어려움

**해결 방법:**
```bash
# 호환성 모드 사용
python cli/main_cli.py --simple
```

### 3. 파일 선택 대화상자가 열리지 않음

**증상:**
- `[F] Upload File` 선택 시 GUI 대화상자 없음
- "GUI file dialog not available" 메시지

**원인:**
- 헤드리스 환경 (GUI 없음)
- tkinter 미설치

**해결 방법:**
```bash
# tkinter 설치 (Ubuntu/Debian)
sudo apt-get install python3-tk

# 또는 수동 경로 입력 사용
# CLI에서 자동으로 수동 입력 모드로 전환됨
```

### 4. 인덱싱이 비활성화됨

**증상:**
- `🗂️ Codebase Indexing: 🔶 Disabled` 표시
- 코드 생성 시 참조 코드가 사용되지 않음

**해결 방법:**
```bash
# 인덱싱 활성화하여 실행
python cli/main_cli.py --enable-indexing

# 또는 대화형 메뉴에서
# [C] Configure -> [T] Toggle Pipeline 선택
```

## 환경 변수

| 변수 | 설명 | 값 |
|------|------|-----|
| `DEEPCODE_CLI_SIMPLE` | 호환성 모드 활성화 | `1` |
| `DEEPCODE_NO_COLOR` | 색상 비활성화 | `1` |
| `NO_COLOR` | 표준 색상 비활성화 | 아무 값 |
| `DEEPCODE_REFERENCE_PATH` | 참조 코드 경로 | 경로 |
| `DEEPCODE_INDEXES_PATH` | 인덱스 경로 | 경로 |

## CLI 옵션

```bash
python cli/main_cli.py --help
```

### 주요 옵션

| 옵션 | 설명 |
|------|------|
| `--simple`, `--compat` | 호환성 모드 (ANSI 코드 비활성화) |
| `--enable-indexing` | 코드 인덱싱 활성화 |
| `--optimized`, `-o` | 최적화 모드 (인덱싱 비활성화) |
| `--verbose`, `-v` | 상세 로그 출력 |
| `--file FILE`, `-f` | 파일 직접 처리 |
| `--url URL`, `-u` | URL 직접 처리 |
| `--chat TEXT`, `-t` | 채팅 입력으로 직접 처리 |

## 터미널 환경별 권장 설정

### VS Code 통합 터미널
```bash
# 일반적으로 정상 동작
python cli/main_cli.py
```

### VS Code Server (웹)
```bash
# 호환성 모드 권장
python cli/main_cli.py --simple
```

### SSH 세션
```bash
# 터미널 타입 확인 후 필요시 호환성 모드
echo $TERM
python cli/main_cli.py --simple
```

### Docker 컨테이너
```bash
# TTY 할당 확인
docker run -it ... python cli/main_cli.py

# TTY 없이 실행 시
docker run ... python cli/main_cli.py --simple
```

## 디버깅 팁

### 1. 터미널 정보 확인
```bash
# 터미널 타입 확인
echo $TERM

# TTY 여부 확인
python -c "import os; print('TTY:', os.isatty(1))"

# 터미널 크기 확인
python -c "import os; print(os.get_terminal_size())"
```

### 2. 환경 확인
```bash
# Python 버전
python --version

# tkinter 설치 확인
python -c "import tkinter; print('tkinter OK')"
```

### 3. 로그 확인
```bash
# 상세 로그 모드
python cli/main_cli.py --verbose 2>&1 | tee debug.log
```

## FAQ

### Q: 호환성 모드에서도 이모지가 보이지 않습니다

A: 터미널의 폰트가 이모지를 지원하지 않을 수 있습니다. 터미널 설정에서 유니코드 지원 폰트로 변경하세요.

### Q: Windows에서 색상이 이상합니다

A: Windows Terminal이나 Windows 10 이상의 콘솔을 사용하세요. 구버전 cmd.exe는 ANSI 코드 지원이 제한적입니다.

### Q: 입력 중 커서가 보이지 않습니다

A: `--simple` 모드를 사용하거나, 입력을 완료하고 Enter를 누르면 정상 동작합니다.

## 지원 요청

문제가 지속되면 다음 정보와 함께 이슈를 생성해주세요:

1. 운영체제 및 버전
2. 터미널 환경 (VS Code, Code Server, SSH 등)
3. Python 버전
4. 오류 메시지 전문
5. 실행한 명령어

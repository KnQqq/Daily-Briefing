# 유튜브 구독 채널 데일리 브리핑봇

매일 아침, 지정한 유튜브 채널의 지난 24시간 신작 영상을 모아 Claude가 요약·편집한 뉴스레터를 텔레그램으로 보내줍니다.
내 컴퓨터를 켜둘 필요 없이 GitHub의 서버에서 자동으로 실행됩니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `bot.py` | 수집 → 요약 → 편집 → 텔레그램 발송 전체 로직 |
| `requirements.txt` | 필요한 파이썬 패키지 목록 |
| `.github/workflows/daily-briefing.yml` | 매일 실행 스케줄 설정 |
| `.gitignore` | `.env` 같은 비밀 파일이 실수로 업로드되는 것을 방지 |

## 설치 순서

### 1. GitHub 저장소 만들기

github.com에 가입(무료)한 뒤 새 저장소를 만듭니다. 이때 **Public(공개)** 으로 만드는 것을 권합니다.
무료 개인 계정의 비공개 저장소에서는 예약 실행이 동작하지 않는 경우가 있기 때문입니다.

공개로 두어도 **API 키는 절대 공개되지 않습니다.** 키는 코드가 아니라 GitHub Secrets에 따로 암호화되어 저장됩니다.
단, `.env` 파일은 절대 업로드하지 마세요. (`.gitignore`가 막아주지만 직접 올리지 않도록 주의)

코드까지 비공개로 하고 싶다면 GitHub Pro(월 4달러)로 업그레이드하면 비공개에서도 예약 실행이 정상 동작합니다.

### 2. 파일 올리기

이 폴더의 파일들을 저장소에 그대로 올립니다. 웹에서 올릴 경우 `Add file > Upload files`를 쓰되,
`.github/workflows/daily-briefing.yml`은 폴더 경로가 중요하므로 `Add file > Create new file`을 눌러
파일명 칸에 `.github/workflows/daily-briefing.yml`을 통째로 입력한 뒤 내용을 붙여넣는 방식이 확실합니다.

### 3. Secrets 등록

저장소의 `Settings > Secrets and variables > Actions > New repository secret`에서 아래 4개를 등록합니다.
이름의 철자가 정확히 일치해야 합니다.

| 이름 | 값 |
|---|---|
| `YOUTUBE_API_KEY` | Google Cloud Console에서 발급한 YouTube Data API v3 키 |
| `ANTHROPIC_API_KEY` | console.anthropic.com에서 발급한 Claude API 키 |
| `TELEGRAM_BOT_TOKEN` | @BotFather에서 발급한 봇 토큰 |
| `TELEGRAM_CHAT_ID` | 내 텔레그램 채팅 ID |

### 4. 바로 실행해서 테스트

예약 시간까지 기다릴 필요 없습니다. 저장소의 `Actions` 탭 → 왼쪽에서 `Daily YouTube Briefing` 선택 →
오른쪽 `Run workflow` 버튼을 누르면 즉시 실행됩니다.
1~2분 뒤 텔레그램에 메시지가 오면 성공입니다.

실패하면 실행 기록을 클릭해서 로그를 보면 어느 단계에서 멈췄는지 바로 보입니다.

## 실행 시간 바꾸기

`daily-briefing.yml`의 `cron` 값은 **UTC 기준**입니다. 원하는 한국시간에서 9를 빼면 됩니다.

```
- cron: '0 23 * * *'   # 한국시간 오전 8:00 (다음날)
- cron: '0 22 * * *'   # 한국시간 오전 7:00
- cron: '0 12 * * *'   # 한국시간 오후 9:00
```

참고로 GitHub의 무료 예약 실행은 서버가 붐빌 때 5~30분 정도 늦게 시작될 수 있습니다. 정시 도착이 꼭 필요하면
원하는 시각보다 30분 앞당겨 설정하세요.

## 채널 추가·변경

`bot.py`의 `target_channels` 목록을 수정하면 됩니다. 채널 ID는 유튜브 채널 페이지 URL이
`youtube.com/channel/UCxxxxxx` 형태일 때 그 `UC...` 부분입니다.
`@핸들` 형태로만 보이는 채널은 채널 페이지에서 `소스 보기` 후 `channelId`를 찾거나, 유튜브 채널 ID 조회 사이트를 이용하세요.

## 알아두면 좋은 점

**비용** — YouTube API는 무료(하루 10,000 쿼터, 채널 4개면 하루 400 소모)입니다. Claude API는 종량제로,
이 규모면 월 1달러 안팎입니다. GitHub Actions는 공개 저장소면 무료입니다.

**실패 알림** — 실행이 실패하면 텔레그램으로 오류 메시지가 오고, GitHub도 메일로 알려줍니다.
봇이 조용히 멈춰 있는데 모르고 지나가는 상황을 방지합니다.

**60일 규칙** — GitHub는 60일간 아무 변경이 없는 저장소의 예약 실행을 자동으로 중지시킵니다.
두 달에 한 번쯤 README에 공백이라도 하나 수정해서 커밋하면 계속 유지됩니다.

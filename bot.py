# -*- coding: utf-8 -*-
"""
유튜브 구독 채널 뉴스레터봇 (Claude API 버전) - GitHub Actions 클라우드 실행판
=============================================================================

기능
----
1) 지정한 유튜브 채널들에서 지난 24시간 신작 영상을 수집합니다. (YouTube Data API v3)
2) 신작 영상 전체를 한 번에 묶어서 Claude API로 요약합니다. (1단계: 개별 요약)
3) 모든 요약을 다시 Claude API에 보내 중복 제거·중요도 정렬된 뉴스레터로 편집합니다. (2단계: 편집장)
4) 완성된 뉴스레터를 텔레그램으로 발송합니다.

로컬 버전에서 달라진 점
-----------------------
- 키를 .env 파일이 아니라 GitHub Secrets(환경변수)에서 읽습니다.
  (.env 파일이 있으면 그것도 그대로 읽으므로, 내 컴퓨터에서도 똑같이 돌아갑니다.)
- 실행 중 예외가 나면 텔레그램으로 오류 알림을 보내고 종료 코드 1로 끝냅니다.
  -> GitHub Actions에서 실행이 '실패'로 표시되고 GitHub가 메일로 알려줍니다.
  -> 봇이 조용히 죽어서 며칠간 모르고 지나가는 상황을 막아줍니다.

필요한 Secrets (저장소 Settings > Secrets and variables > Actions)
------------------------------------------------------------------
    YOUTUBE_API_KEY      # YouTube Data API v3 전용 키 (console.cloud.google.com)
    ANTHROPIC_API_KEY    # Claude API 키 (console.anthropic.com)
    TELEGRAM_BOT_TOKEN   # @BotFather 에서 발급
    TELEGRAM_CHAT_ID     # 본인 채팅 ID

주의사항
--------
- YouTube Data API의 search.list는 호출당 쿼터 100 단위를 소모합니다.
  채널 수가 아주 많다면(하루 100개 이상) 쿼터를 아끼는 방식(uploads 재생목록 활용)으로
  구조를 바꾸는 게 좋습니다. 채널이 몇 개~수십 개 수준이면 지금 방식으로 충분합니다.
"""

import os
import re
import sys
import json
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests
import anthropic

# .env 파일은 내 컴퓨터에서 돌릴 때만 쓰입니다.
# GitHub Actions에는 .env가 없지만, 없어도 에러 없이 그냥 넘어갑니다.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

_required = {
    "YOUTUBE_API_KEY": YOUTUBE_API_KEY,
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
}
_missing = [name for name, value in _required.items() if not value]
if _missing:
    raise RuntimeError(
        f"다음 값이 설정되어 있지 않습니다: {', '.join(_missing)}\n"
        "GitHub에서 실행 중이라면 저장소 Settings > Secrets and variables > Actions 에 등록하세요.\n"
        "내 컴퓨터에서 실행 중이라면 같은 폴더에 .env 파일을 만들고 값을 채워주세요."
    )

claude_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
CLAUDE_MODEL_SUMMARY = "claude-opus-5"     # 1단계: 영상 요약
CLAUDE_MODEL_EDITOR = "claude-opus-5"      # 2단계: 편집장 큐레이션


# =========================================================
# 1. 유튜브에서 지난 24시간 신작 영상 수집
# =========================================================

def get_recent_videos():
    print("🎬 유튜브 구독 채널에서 지난 24시간 신작 수집 중...")
    time_threshold = (datetime.now(timezone.utc) - timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')

    # 채널 ID는 반드시 실제 값으로 확인 후 넣으세요. (youtube.com/channel/UC... 형태의 URL에서 확인)
    target_channels = [
        {"name": "엔비디아 공식", "id": "UCHuiy8bXnmK5nisYHUd1J5g"},
        {"name": "슈카월드", "id": "UCsJ6RuBiTVWRX156FVbeaGg"},
        {"name": "Bloomberg TV", "id": "UCIALMKvObZNtJ6AmdCLP7Lg"},
        {"name": "CNBC TV", "id": "UCrp_UI8XtuYfpiqluWLD7Lw"}
    ]

    collected_videos = []
    for channel in target_channels:
        url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "key": YOUTUBE_API_KEY,
            "channelId": channel["id"],
            "part": "snippet",
            "order": "date",
            "maxResults": 15,
            "publishedAfter": time_threshold,
            "type": "video"
        }
        try:
            response = requests.get(url, params=params, timeout=30)
            if response.status_code != 200:
                print(f"❌ {channel['name']} 채널 연결 실패 (에러 코드: {response.status_code})")
                print(f"   상세 내용: {response.text[:300]}")
                continue
            data = response.json()
            if "items" in data:
                for item in data["items"]:
                    collected_videos.append({
                        "channel_name": channel["name"],
                        "title": item["snippet"]["title"],
                        "description": item["snippet"]["description"],
                        "link": f"https://youtu.be/{item['id']['videoId']}"
                    })
        except Exception as e:
            print(f"❌ {channel['name']} 네트워크 예외: {str(e)}")

    print(f"✅ 총 {len(collected_videos)}개의 신작 영상 발견.")
    return collected_videos


# =========================================================
# 2. Claude API 호출 공통 함수 (재시도 포함)
# =========================================================

def call_claude(prompt: str, model: str = CLAUDE_MODEL_SUMMARY, max_tokens: int = 2000, max_retries: int = 2) -> str:
    """
    Claude API를 호출해서 응답 텍스트를 반환합니다.
    429(속도 제한) 에러가 나면 지수적으로 대기 시간을 늘려가며 재시도합니다.
    """
    for attempt in range(max_retries + 1):
        try:
            response = claude_client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            # 응답 블록 중 ThinkingBlock(추론 과정)은 건너뛰고 실제 TextBlock만 사용
            text_blocks = [block.text for block in response.content if block.type == "text"]
            return "".join(text_blocks).strip()

        except anthropic.RateLimitError:
            if attempt < max_retries:
                wait_seconds = 10 * (attempt + 1)  # 10초, 20초, 30초... 점점 늘려가며 대기
                print(f"   ⏳ 속도 제한, {wait_seconds}초 대기 후 재시도...")
                time.sleep(wait_seconds)
                continue
            raise

        except anthropic.APIStatusError as e:
            print(f"   ❌ Claude API 오류 (코드 {e.status_code}): {e.message}")
            raise


# =========================================================
# 3. 1단계 에이전트: 영상 전체를 한 번에 묶어서 요약
# =========================================================

def analyze_and_summarize_individually(videos):
    """
    영상 하나마다 Claude를 따로따로 부르지 않고, 전체 영상을 한 번의 프롬프트에
    묶어서 딱 1번만 호출합니다. (호출 횟수를 아끼고 속도도 빠릅니다)
    응답은 JSON 배열 형식으로 받아서, 각 영상에 정확히 매칭시킵니다.
    """
    if not videos:
        return []

    print(f"🤖 1단계 에이전트: 영상 {len(videos)}개를 한 번에 묶어서 요약 요청 (Claude)...")

    video_list_text = "\n".join(
        f"{i}. 채널: {v['channel_name']} | 제목: {v['title']} | 설명: {v['description'][:300]}"
        for i, v in enumerate(videos)
    )
    prompt = (
        "아래는 유튜브 영상 목록이야. 각 영상을 2~3줄로 요약해줘.\n"
        "반드시 아래 JSON 배열 형식으로만 응답하고, 다른 설명은 절대 붙이지 마. "
        "코드블록(```)도 쓰지 말고 순수 JSON 텍스트만 출력해.\n"
        '[{"index": 0, "summary": "..."}, {"index": 1, "summary": "..."}, ...]\n\n'
        f"{video_list_text}"
    )

    summarized_list = []
    try:
        raw_text = call_claude(prompt, max_tokens=4000)
        raw_text = re.sub(r"^```json|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
        summaries = json.loads(raw_text)
        summary_by_index = {item["index"]: item["summary"] for item in summaries}

        for i, video in enumerate(videos):
            summarized_list.append({
                "channel_name": video["channel_name"],
                "title": video["title"],
                "link": video["link"],
                "summary": summary_by_index.get(i, "(이 영상의 요약을 받지 못했습니다)")
            })
        print(f"   ✅ {len(summarized_list)}개 영상 요약 완료 (Claude 호출 1회)")

    except Exception as e:
        print(f"   ❌ 배치 요약 실패: {e}")
        # 실패하더라도 제목만이라도 뉴스레터에 남도록 처리
        for video in videos:
            summarized_list.append({
                "channel_name": video["channel_name"],
                "title": video["title"],
                "link": video["link"],
                "summary": "(요약 실패: 제목만 표시됩니다)"
            })

    return summarized_list


# =========================================================
# 4. 2단계 에이전트: 편집장 - 전체 요약을 큐레이션
# =========================================================

def edit_and_curate_newsletter(summarized_videos):
    if not summarized_videos:
        return "종합 결과: 지난 24시간 동안 새로 업로드된 영상이 없습니다."

    print("🧠 2단계 에이전트: 편집장 모드 가동 및 최종 큐레이션 (Claude)...")

    aggregated_data = ""
    for v in summarized_videos:
        aggregated_data += f"채널: {v['channel_name']}\n제목: {v['title']}\n링크: {v['link']}\n{v['summary']}\n------------------\n"

    kst_today = datetime.now(timezone(timedelta(hours=9)))
    today_str = kst_today.strftime("%Y년 %m월 %d일")

    prompt = (
        "너는 뉴스레터 편집장이야. 아래 유튜브 요약본들을 중복 제거하고 "
        "중요도 순으로 정렬해서 뉴스레터 양식으로 작성해줘. 각 소식에는 링크도 포함해줘.\n"
        f"오늘 날짜는 {today_str}이야. 뉴스레터 제목이나 본문에 연도·날짜를 표기할 때는 "
        f"반드시 이 날짜를 기준으로 쓰고, 다른 연도를 추측해서 쓰지 마.\n\n"
        f"{aggregated_data}"
    )

    try:
        return call_claude(prompt, model=CLAUDE_MODEL_EDITOR, max_tokens=3000)
    except Exception as e:
        return f"❌ 최종 편집장 에이전트 구동 실패: {e}"


# =========================================================
# 5. 텔레그램 발송
# =========================================================

def send_to_telegram(text):
    """
    발송에 실패하면(네트워크 오류든, 토큰/chat_id가 잘못됐든) 예외를 raise합니다.
    예전에는 실패해도 print만 하고 조용히 넘어가서, GitHub Actions에는
    '성공'으로 표시되는데 실제로는 메시지가 안 오는 문제가 있었습니다.
    이제는 실패 시 main()의 except로 넘어가 실행 자체가 '실패'로 표시되고,
    notify_error()로 오류 알림도 시도합니다.
    """
    print("🚀 텔레그램으로 최종 브리핑 뉴스레터 발송 중...")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)] or [text]

    for chunk in chunks:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "Markdown"}
        try:
            res = requests.post(url, json=payload, timeout=30)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"텔레그램 연동 오류: {e}") from e

        if res.status_code == 200:
            continue

        # 마크다운 파싱 실패(짝 안 맞는 *, _, [ 등)면 일반 텍스트로 재전송
        if res.status_code == 400 and "can't parse entities" in res.text:
            print("   ⚠️ 마크다운 파싱 실패, 일반 텍스트로 재전송...")
            try:
                res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=30)
            except requests.exceptions.RequestException as e:
                raise RuntimeError(f"텔레그램 연동 오류: {e}") from e
            if res.status_code == 200:
                continue

        raise RuntimeError(f"발송 실패 (코드 {res.status_code}): {res.text}")

    print("✨ 텔레그램 뉴스레터 발송 완료!")


def notify_error(err: Exception):
    """
    봇이 통째로 실패했을 때 텔레그램으로 짧은 오류 알림을 보냅니다.
    클라우드에서 무인으로 돌기 때문에, 조용히 죽는 것을 막기 위한 안전장치입니다.
    """
    kst_now = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    message = (
        f"⚠️ 유튜브 브리핑봇 실행 실패\n"
        f"시각: {kst_now} (KST)\n"
        f"오류: {type(err).__name__}: {str(err)[:500]}"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message},
            timeout=30,
        )
    except Exception:
        pass  # 알림마저 실패하면 조용히 넘어갑니다. (로그에는 이미 남아 있음)


if __name__ == "__main__":
    try:
        raw_videos = get_recent_videos()
        summarized_data = analyze_and_summarize_individually(raw_videos)
        final_briefing = edit_and_curate_newsletter(summarized_data)
        send_to_telegram(final_briefing)
    except Exception as e:
        print("💥 실행 중 오류가 발생했습니다.")
        traceback.print_exc()
        notify_error(e)
        sys.exit(1)  # GitHub Actions에서 '실패'로 표시되도록 종료 코드를 남깁니다.

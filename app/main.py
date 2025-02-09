from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
import re
import os
from dotenv import load_dotenv
from claude_service import ClaudeService

load_dotenv()

app = FastAPI()
claude_service = ClaudeService()

# CORS設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# カレンダーAPI設定
SCOPES = ['https://www.googleapis.com/auth/calendar']

class ChatMessage(BaseModel):
    message: str

def get_calendar_service():
    credentials = service_account.Credentials.from_service_account_file(
        'app/service_account.json',
        scopes=SCOPES
    )
    return build('calendar', 'v3', credentials=credentials)

@app.post("/calendar/chat")
async def handle_chat(message: ChatMessage):
    try:
        msg = message.message
        
        # 日付と時間範囲の抽出
        date_patterns = [
            r'(\d+)月(\d+)日',  # MM月DD日
            r'明日',            # 明日
            r'明後日',          # 明後日
            r'来週の(月|火|水|木|金|土|日)曜日'  # 来週の曜日
        ]
        time_patterns = [
            r'(\d+)時から(\d+)時',      # HH時からHH時
            r'午前(\d+)時から午後(\d+)時',  # 午前/午後
            r'(\d+)時半'               # 時半
        ]
        
        # 日付のマッチング
        date_match = None
        for pattern in date_patterns:
            match = re.search(pattern, msg)
            if match:
                date_match = match
                break
        
        # 時間のマッチング
        time_match = None
        for pattern in time_patterns:
            match = re.search(pattern, msg)
            if match:
                time_match = match
                break
        
        # より具体的なエラーメッセージを提供
        if not date_match:
            return {
                "response": "申し訳ありません。日付の指定を理解できませんでした。\n"
                           "以下のような形式で指定してください：\n"
                           "・2月12日\n"
                           "・明日\n"
                           "・明後日\n"
                           "・来週の月曜日"
            }
            
        if not time_match:
            return {
                "response": "申し訳ありません。時間の指定を理解できませんでした。\n"
                           "以下のような形式で指定してください：\n"
                           "・13時から16時\n"
                           "・午前10時から午後3時\n"
                           "・15時半"
            }
            
        # 日付の設定
        current_date = datetime.now()
        current_year = current_date.year
        
        if '明日' in msg:
            target_date = current_date + timedelta(days=1)
            month = target_date.month
            day = target_date.day
        elif '明後日' in msg:
            target_date = current_date + timedelta(days=2)
            month = target_date.month
            day = target_date.day
        elif '来週' in msg:
            # 曜日を数値に変換 (月=0, 火=1, ...)
            weekday_map = {'月': 0, '火': 1, '水': 2, '木': 3, '金': 4, '土': 5, '日': 6}
            weekday = weekday_map[date_match.group(1)]
            
            # 現在の曜日から目標の曜日までの日数を計算
            days_ahead = weekday - current_date.weekday()
            if days_ahead <= 0:  # 次の週の同じ曜日
                days_ahead += 7
            target_date = current_date + timedelta(days=days_ahead + 7)  # +7 for next week
            month = target_date.month
            day = target_date.day
        else:
            # MM月DD日 形式の場合
            month = int(date_match.group(1))
            day = int(date_match.group(2))
        
        # 時間の設定と検証
        if '午前' in msg and '午後' in msg:
            start_hour = int(time_match.group(1))  # 午前の時間
            end_hour = int(time_match.group(2)) + 12  # 午後の時間は12を加算
        elif '時半' in msg:
            time_str = time_match.group(1)
            start_hour = int(time_str)
            end_hour = start_hour + 1  # 30分は1時間として扱う
        else:
            start_hour = int(time_match.group(1))
            end_hour = int(time_match.group(2))
        
        # 基本的な入力値の検証（より具体的なエラーメッセージ）
        if not (1 <= month <= 12):
            return {
                "response": "申し訳ありません。指定された月が無効です。\n"
                           f"指定された月: {month}月\n"
                           "1月から12月の間で指定してください。"
            }
            
        if not (1 <= day <= 31):
            return {
                "response": "申し訳ありません。指定された日が無効です。\n"
                           f"指定された日: {day}日\n"
                           "1日から31日の間で指定してください。"
            }
            
        if not (0 <= start_hour <= 23):
            return {
                "response": "申し訳ありません。指定された開始時刻が無効です。\n"
                           f"指定された時刻: {start_hour}時\n"
                           "0時から23時の間で指定してください。"
            }
            
        if not (0 <= end_hour <= 23):
            return {
                "response": "申し訳ありません。指定された終了時刻が無効です。\n"
                           f"指定された時刻: {end_hour}時\n"
                           "0時から23時の間で指定してください。"
            }
            
        if start_hour >= end_hour:
            return {
                "response": "申し訳ありません。終了時刻は開始時刻より後の時間を指定してください。\n"
                           f"指定された時間帯: {start_hour}時から{end_hour}時\n"
                           "例：13時から16時"
            }
            
        # 日付の妥当性チェック
        try:
            target_date = datetime(current_year, month, day)
            if target_date.date() < datetime.now().date():
                return {
                    "response": "申し訳ありません。過去の日付は指定できません。\n"
                               "今日以降の日付を指定してください。"
                }
        except ValueError:
            return {
                "response": "申し訳ありません。指定された日付は存在しません。\n"
                           "正しい日付を指定してください。"
            }
        
        # 時間範囲の設定（既に上で設定済みのため削除）
        
        # タイムゾーン設定
        JST = '+09:00'
        TIMEZONE = 'Asia/Tokyo'
        
        # 時間範囲の妥当性チェック
        if end_hour - start_hour < 1:
            return {
                "response": "申し訳ありません。指定された時間範囲が短すぎます。\n"
                           "少なくとも1時間以上の時間範囲を指定してください。"
            }
        
        if end_hour - start_hour > 12:
            return {
                "response": "申し訳ありません。指定された時間範囲が長すぎます。\n"
                           "12時間以内の時間範囲を指定してください。"
            }
        
        # 営業時間チェック（9時から18時）
        if start_hour < 9 or end_hour > 18:
            return {
                "response": "申し訳ありません。営業時間外の時間帯が指定されています。\n"
                           "営業時間（9時から18時）内の時間帯を指定してください。"
            }
        
        # 開始時刻と終了時刻の設定（JSTで設定）
        start_time = datetime(current_year, month, day, start_hour, 0, 0)
        end_time = datetime(current_year, month, day, end_hour, 0, 0)
        current_time = start_time
        meeting_duration = timedelta(hours=1)
        
        # カレンダーサービスの取得
        calendar_service = get_calendar_service()
        
        # FreeBusy APIを使用して空き時間を取得
        calendar_id = os.getenv('CALENDAR_ID', 'us.tomoki17@gmail.com')
        body = {
            'timeMin': start_time.isoformat() + JST,
            'timeMax': end_time.isoformat() + JST,
            'timeZone': TIMEZONE,
            'items': [{'id': calendar_id}]
        }
        
        freebusy_response = calendar_service.freebusy().query(body=body).execute()
        busy_slots = freebusy_response['calendars'][calendar_id]['busy']
        
        # Claude APIで最適な時間枠を取得
        slot_suggestion = await claude_service.analyze_free_slots(
            busy_slots,
            start_time,
            end_time,
            calendar_id
        )
        
        if not slot_suggestion:
            return {
                "response": "申し訳ありません。指定された時間範囲内に適切な空き時間が見つかりませんでした。\n"
                           "別の時間帯をお試しください。"
            }
            
        # 予定を作成
        event = {
            'summary': '会議',
            'start': {
                'dateTime': slot_suggestion['suggested_time']['start'],
                'timeZone': TIMEZONE
            },
            'end': {
                'dateTime': slot_suggestion['suggested_time']['end'],
                'timeZone': TIMEZONE
            }
        }
        
        try:
            # アトミックな操作として予定を作成
            created_event = calendar_service.events().insert(
                calendarId=calendar_id,
                body=event
            ).execute()
            
            return {
                "response": f"以下の時間に会議を登録しました：\n"
                           f"{slot_suggestion['reason']}\n"
                           f"予定のリンク：{created_event.get('htmlLink')}"
            }
        except Exception as e:
            print(f"Event creation error: {str(e)}")
            return {
                "response": "申し訳ありません。予定の登録に失敗しました。\n"
                           "以下のいずれかの理由により登録できませんでした：\n"
                           "・指定された時間帯が既に予約されています\n"
                           "・カレンダーへのアクセス権限の問題\n"
                           "・ネットワークエラー\n"
                           "・システムエラー\n\n"
                           "別の時間帯を指定するか、しばらく待ってから、もう一度お試しください。"
            }
        
        # すべての時間枠をチェックしても空き時間が見つからなかった場合
        return {
            "response": "申し訳ありません。指定された時間範囲内（" + 
                       f"{start_time.strftime('%H:%M')}から{end_time.strftime('%H:%M')}まで）に\n" +
                       "1時間の空き時間が見つかりませんでした。\n" +
                       "別の時間帯をお試しください。"
        }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        error_time = f"{start_time.strftime('%Y年%m月%d日 %H:%M')}から{end_time.strftime('%H:%M')}まで"
        return {
            "response": "申し訳ありません。予定の登録に失敗しました。\n"
                       f"指定された時間帯（{error_time}）で以下のいずれかの理由により登録できませんでした：\n"
                       "・指定された時間帯が既に予約されています\n"
                       "・カレンダーへのアクセス権限の問題\n"
                       "・ネットワークエラー\n"
                       "・システムエラー\n\n"
                       "別の時間帯を指定するか、しばらく待ってから、もう一度お試しください。"
        }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

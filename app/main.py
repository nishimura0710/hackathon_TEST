from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
import re

app = FastAPI()

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
        
        # 日付と時間範囲の抽出（例：2月12日の12時から16時）
        date_match = re.search(r'(\d+)月(\d+)日', msg)
        time_range_match = re.search(r'(\d+)時から(\d+)時', msg)
        
        if not date_match or not time_range_match:
            return {
                "response": "すみません、日時を理解できませんでした。\n"
                           "例：「2月12日の12時から16時で空いてる時間に会議を入れて」のように教えてください。\n"
                           "※ 日付と時間は数字で指定してください。"
            }
            
        # 日付の設定
        month = int(date_match.group(1))
        day = int(date_match.group(2))
        current_year = datetime.now().year
        
        # 時間の設定と検証
        start_hour = int(time_range_match.group(1))
        end_hour = int(time_range_match.group(2))
        
        # 基本的な入力値の検証
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return {
                "response": "申し訳ありません。正しい日付を指定してください。\n"
                           "月は1-12、日は1-31の範囲で指定してください。"
            }
            
        if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
            return {
                "response": "申し訳ありません。正しい時間を指定してください。\n"
                           "時間は0-23の範囲で指定してください。"
            }
            
        if start_hour >= end_hour:
            return {
                "response": "申し訳ありません。終了時間は開始時間より後の時間を指定してください。"
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
        
        # 時間範囲の設定
        start_hour = int(time_range_match.group(1))
        end_hour = int(time_range_match.group(2))
        
        # 開始時刻と終了時刻の設定（JSTで設定）
        start_time = datetime(current_year, month, day, start_hour, 0, 0)
        end_time = datetime(current_year, month, day, end_hour, 0, 0)
        current_time = start_time
        meeting_duration = timedelta(hours=1)
        
        # カレンダーサービスの取得
        calendar_service = get_calendar_service()
        
        # FreeBusy APIを使用して空き時間を取得
        body = {
            'timeMin': start_time.isoformat() + '+09:00',
            'timeMax': end_time.isoformat() + '+09:00',
            'timeZone': 'Asia/Tokyo',
            'items': [{'id': 'us.tomoki17@gmail.com'}]
        }
        
        freebusy_response = calendar_service.freebusy().query(body=body).execute()
        busy_slots = freebusy_response['calendars']['us.tomoki17@gmail.com']['busy']
        
        # 空き時間を探す（1時間の会議を想定）
        meeting_duration = timedelta(hours=1)
        free_slots = []
        current_time = start_time
        
        # 最初の空き時間を確認
        if not busy_slots:
            free_slots.append((current_time, end_time))
        else:
            # 最初のbusy slotまでの空き時間を確認
            first_busy_start = datetime.fromisoformat(busy_slots[0]['start'].replace('Z', '+00:00'))
            if current_time + meeting_duration <= first_busy_start:
                free_slots.append((current_time, first_busy_start))
            
            # busy slotsの間の空き時間を確認
            for i in range(len(busy_slots)):
                current_busy_end = datetime.fromisoformat(busy_slots[i]['end'].replace('Z', '+00:00'))
                next_busy_start = datetime.fromisoformat(busy_slots[i + 1]['start'].replace('Z', '+00:00')) if i + 1 < len(busy_slots) else end_time
                
                if current_busy_end + meeting_duration <= next_busy_start:
                    free_slots.append((current_busy_end, next_busy_start))
            
            # 空き時間が見つかった場合、最初の空き時間に予定を登録
            if free_slots:
                slot_start, slot_end = free_slots[0]
                event = {
                    'summary': '会議',
                    'start': {
                        'dateTime': slot_start.isoformat() + '+09:00',
                        'timeZone': 'Asia/Tokyo'
                    },
                    'end': {
                        'dateTime': (slot_start + meeting_duration).isoformat() + '+09:00',
                        'timeZone': 'Asia/Tokyo'
                    }
                }
                
                created_event = calendar_service.events().insert(
                    calendarId='us.tomoki17@gmail.com',
                    body=event
                ).execute()
                
                return {
                    "response": f"以下の空き時間に会議を登録しました：\n"
                               f"日時：{slot_start.strftime('%Y年%m月%d日 %H:%M')}から"
                               f"{(slot_start + meeting_duration).strftime('%H:%M')}まで\n"
                               f"予定のリンク：{created_event.get('htmlLink')}"
                }
        
        return {
            "response": "申し訳ありません。指定された時間範囲内（" + 
                       f"{start_time.strftime('%H:%M')}から{end_time.strftime('%H:%M')}まで）に\n" +
                       "1時間の空き時間が見つかりませんでした。\n" +
                       "別の時間帯をお試しください。"
        }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return {
            "response": "申し訳ありません。予定の登録に失敗しました。\n"
                       "以下のいずれかの理由が考えられます：\n"
                       "・カレンダーへのアクセス権限の問題\n"
                       "・ネットワークエラー\n"
                       "・システムエラー\n\n"
                       "しばらく待ってから、もう一度お試しください。"
        }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

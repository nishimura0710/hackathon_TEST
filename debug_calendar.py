from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
import json

def get_calendar_service():
    credentials = service_account.Credentials.from_service_account_file(
        'app/service_account.json',
        scopes=['https://www.googleapis.com/auth/calendar']
    )
    return build('calendar', 'v3', credentials=credentials)

def check_free_busy(start_time, end_time, description=""):
    service = get_calendar_service()
    
    # タイムゾーン情報を追加
    jst_timezone = '+09:00'
    start_time = start_time.replace(tzinfo=None)  # タイムゾーン情報をクリア
    end_time = end_time.replace(tzinfo=None)  # タイムゾーン情報をクリア
    
    print(f"\n=== {description} ===")
    print(f"Checking free/busy from {start_time} to {end_time} (JST)")
    
    body = {
        'timeMin': start_time.isoformat() + jst_timezone,
        'timeMax': end_time.isoformat() + jst_timezone,
        'timeZone': 'Asia/Tokyo',
        'items': [{'id': 'us.tomoki17@gmail.com'}]
    }
    
    freebusy_response = service.freebusy().query(body=body).execute()
    busy_slots = freebusy_response['calendars']['us.tomoki17@gmail.com']['busy']
    
    print("\nBusy slots:")
    print(json.dumps(busy_slots, indent=2, ensure_ascii=False))
    
    # 空き時間を計算
    free_slots = []
    current_time = start_time
    
    # 最初の空き時間
    if not busy_slots:
        free_slots.append({
            'start': current_time.isoformat() + jst_timezone,
            'end': end_time.isoformat() + jst_timezone
        })
    
    # 予定と予定の間の空き時間
    meeting_duration = timedelta(hours=1)  # 1時間の会議を想定
    for i in range(len(busy_slots)):
        current_busy_end = datetime.fromisoformat(busy_slots[i]['end'].replace('Z', '+00:00')).replace(tzinfo=None)
        next_busy_start = datetime.fromisoformat(busy_slots[i + 1]['start'].replace('Z', '+00:00')).replace(tzinfo=None) if i + 1 < len(busy_slots) else end_time
        
        if current_busy_end + meeting_duration <= next_busy_start:
            free_slots.append({
                'start': current_busy_end.isoformat() + jst_timezone,
                'end': next_busy_start.isoformat() + jst_timezone
            })
    
    print("\nFree slots:")
    print(json.dumps(free_slots, indent=2, ensure_ascii=False))
    
    return {'busy': busy_slots, 'free': free_slots}

def run_tests():
    # テストケース1: 既存の予定がある時間帯
    start1 = datetime(2025, 2, 12, 12, 0, 0)
    end1 = datetime(2025, 2, 12, 14, 0, 0)
    result1 = check_free_busy(start1, end1, "Test Case 1: Existing Events (12:00-14:00)")
    print(f"Number of busy slots: {len(result1['busy'])}")
    print(f"Number of free slots: {len(result1['free'])}")
    
    # テストケース2: 空き時間がある時間帯
    start2 = datetime(2025, 2, 12, 14, 0, 0)
    end2 = datetime(2025, 2, 12, 16, 0, 0)
    result2 = check_free_busy(start2, end2, "Test Case 2: Free Time Slots (14:00-16:00)")
    print(f"Number of busy slots: {len(result2['busy'])}")
    print(f"Number of free slots: {len(result2['free'])}")
    
    # テストケース3: 翌日の時間帯（任意の日付）
    start3 = datetime(2025, 2, 13, 10, 0, 0)
    end3 = datetime(2025, 2, 13, 12, 0, 0)
    result3 = check_free_busy(start3, end3, "Test Case 3: Next Day (10:00-12:00)")
    print(f"Number of busy slots: {len(result3['busy'])}")
    print(f"Number of free slots: {len(result3['free'])}")
    
    # テストケース4: 空き時間の検出（13時から16時で14時から15時が空いている場合）
    start4 = datetime(2025, 2, 12, 13, 0, 0)
    end4 = datetime(2025, 2, 12, 16, 0, 0)
    result4 = check_free_busy(start4, end4, "Test Case 4: Specific Free Slot (14:00-15:00)")
    print(f"Number of busy slots: {len(result4['busy'])}")
    print(f"Number of free slots: {len(result4['free'])}")

if __name__ == '__main__':
    run_tests()

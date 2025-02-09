from datetime import datetime, timedelta
from anthropic import Anthropic
import os
from typing import Dict, List, Optional
import json

class ClaudeService:
    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        self.client = Anthropic(api_key=api_key)
    
    def analyze_free_slots(
        self,
        busy_slots: List[Dict],
        start_time: datetime,
        end_time: datetime,
        calendar_id: str
    ) -> Optional[Dict]:
        """Find the best available time slot for a meeting."""
        calendar_data = {
            "calendars": {
                calendar_id: {
                    "busy": busy_slots
                }
            }
        }
        
        prompt = f"""
次のデータは Google Calendar の busy 時間です。空いている時間のリストを作成し、1時間の最適な時間を提案してください。

指定された範囲: {start_time.strftime("%Y-%m-%d %H:%M")} 〜 {end_time.strftime("%Y-%m-%d %H:%M")}
Busyデータ: {json.dumps(calendar_data, ensure_ascii=False)}

条件:
- 最も早い時間に予約
- 1時間の会議時間を確保
- タイムゾーン：日本時間（JST/UTC+9）
- 営業時間：午前9時から午後6時まで
- 既存の予定と重複しない
- 予定の前後に15分以上の余裕を確保
- 時間の説明は日本語で詳しく

出力形式:
{{
  "suggested_time": {{
    "start": "YYYY-MM-DDTHH:MM:SS+09:00",
    "end": "YYYY-MM-DDTHH:MM:SS+09:00"
  }},
  "reason": "選択理由（日本語で説明）"
}}
"""
        
        try:
            response = self.client.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            text = response.content[0].text
            json_start = text.find('{')
            json_end = text.rfind('}') + 1
            
            if json_start >= 0 and json_end > json_start:
                slot_data = json.loads(text[json_start:json_end])
                if self._validate_slot(slot_data, start_time, end_time, busy_slots):
                    return slot_data
            
            return None
            
        except Exception as e:
            print(f"Claude APIエラー: {str(e)}")
            return None
            
    def _validate_slot(
        self,
        result: Dict,
        start_time: datetime,
        end_time: datetime,
        busy_slots: List[Dict]
    ) -> bool:
        """Validate the suggested time slot."""
        try:
            suggested = result.get("suggested_time", {})
            if not suggested:
                return False
            
            # Parse times and normalize to naive datetime
            slot_start = datetime.fromisoformat(suggested["start"]).replace(tzinfo=None)
            slot_end = datetime.fromisoformat(suggested["end"]).replace(tzinfo=None)
            start_time = start_time.replace(tzinfo=None)
            end_time = end_time.replace(tzinfo=None)
            
            # Basic validation checks
            if not (9 <= slot_start.hour < 18 and 9 <= slot_end.hour <= 18):
                return False
            
            if slot_end - slot_start != timedelta(hours=1):
                return False
            
            if slot_start < start_time or slot_end > end_time:
                return False
            
            # Check for conflicts with existing events
            buffer = timedelta(minutes=15)
            for busy in busy_slots:
                busy_start = datetime.fromisoformat(busy["start"].replace('Z', '+00:00')).replace(tzinfo=None)
                busy_end = datetime.fromisoformat(busy["end"].replace('Z', '+00:00')).replace(tzinfo=None)
                
                if (slot_start < busy_end and slot_end > busy_start):
                    return False
                
                if abs(slot_start - busy_end) < buffer or abs(slot_end - busy_start) < buffer:
                    return False
            
            return True
            
        except Exception:
            return False

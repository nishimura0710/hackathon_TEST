from datetime import datetime, timedelta
import anthropic
import os
from typing import Dict, List, Optional
import json

class ClaudeService:
    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        self.client = anthropic.Anthropic(
            api_key=api_key
        )
    
    async def analyze_free_slots(
        self,
        busy_slots: List[Dict],
        start_time: datetime,
        end_time: datetime,
        calendar_id: str
    ) -> Optional[Dict]:
        calendar_data = {
            "calendars": {
                calendar_id: {
                    "busy": busy_slots
                }
            }
        }
        
        prompt = f"""
        次のJSONはGoogleカレンダーのfreeBusy.queryのレスポンスです。
        この中で空いている時間帯を見つけ、最適な1時間のスロットを提案してください。

        条件:
        - 営業時間：午前9時から午後6時まで
        - できるだけ早い時間のスロットを優先
        - 1時間の会議時間を確保
        - 既存の予定と重複しないこと
        - 予定と予定の間に15分以上の余裕を確保
        - 時間の説明は日本語で詳しく（例：午前10時から午前11時まで）

        選択理由も日本語で詳しく説明してください。
        例：「この時間帯を選んだ理由：
        ・営業時間内の早い時間帯
        ・前後の予定と15分以上の間隔あり
        ・1時間の会議時間を確保可能」

        JSON:
        {json.dumps(calendar_data, ensure_ascii=False, indent=2)}
        
        レスポンスは以下のJSON形式で返してください：
        {{
          "suggested_time": {{
            "start": "YYYY-MM-DDTHH:MM:SS+09:00",
            "end": "YYYY-MM-DDTHH:MM:SS+09:00"
          }},
          "reason": "選択理由"
        }}
        """
        
        try:
            response = await self.client.messages.create(
                model="claude-3-opus-20240229",
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": prompt
                }]
            )
                
            try:
                content = response.content[0].text
                slot_data = json.loads(content)
                
                if self._validate_slot(slot_data, start_time, end_time, busy_slots):
                    return slot_data
                return None
            except (json.JSONDecodeError, KeyError, IndexError, AttributeError) as e:
                print(f"Failed to parse Claude response: {str(e)}")
                print(f"Raw response: {response}")
                return None
            
        except Exception as e:
            print(f"Claude API error: {str(e)}")
            return None
            
    def _validate_slot(
        self,
        result: Dict,
        start_time: datetime,
        end_time: datetime,
        busy_slots: List[Dict]
    ) -> bool:
        try:
            # Validate suggested time slot
            suggested = result.get("suggested_time", {})
            if not suggested:
                print("スロット提案が見つかりません")
                return False
                
            try:
                slot_start = datetime.fromisoformat(suggested.get("start")).replace(tzinfo=None)
                slot_end = datetime.fromisoformat(suggested.get("end")).replace(tzinfo=None)
            except (ValueError, TypeError) as e:
                print(f"日時のパース中にエラーが発生しました: {str(e)}")
                return False
            
            # Check business hours (9:00-18:00)
            if not (9 <= slot_start.hour < 18 and 9 <= slot_end.hour <= 18):
                print("営業時間外の時間帯が提案されました")
                return False
                
            # Strictly validate slot duration (exactly 1 hour)
            if slot_end - slot_start != timedelta(hours=1):
                print("提案された時間枠が1時間ではありません")
                return False
                
            # Check within requested time range
            if slot_start < start_time or slot_end > end_time:
                print("提案された時間枠が指定された範囲外です")
                return False
                
            # Check for overlaps and minimum buffer with ALL existing events
            buffer = timedelta(minutes=15)
            for busy in busy_slots:
                busy_start = datetime.fromisoformat(busy["start"]).replace(tzinfo=None)
                busy_end = datetime.fromisoformat(busy["end"]).replace(tzinfo=None)
                
                # Strict overlap check
                if (slot_start < busy_end and slot_end > busy_start):
                    print("既存の予定と重複しています")
                    return False
                
                # Ensure minimum 15-minute buffer before and after
                if abs(slot_start - busy_end) < buffer or abs(slot_end - busy_start) < buffer:
                    print("既存の予定との間隔が15分未満です")
                    return False
                    
            return True
        except Exception as e:
            print(f"スロット検証エラー: {str(e)}")  # Changed to Japanese for consistency
            return False

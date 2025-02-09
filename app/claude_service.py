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
        - 営業時間：9:00から18:00まで
        - できるだけ早い時間のスロットを優先
        - 1時間の時間を確保
        - 既存の予定と重複しない
        - 予定と予定の間に十分な空きがある時間帯を選択

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
            slot_start = datetime.fromisoformat(suggested.get("start")).replace(tzinfo=None)
            slot_end = datetime.fromisoformat(suggested.get("end")).replace(tzinfo=None)
            
            # Check business hours
            if not (9 <= slot_start.hour < 18 and 9 <= slot_end.hour <= 18):
                return False
                
            # Check slot duration
            if slot_end - slot_start != timedelta(hours=1):
                return False
                
            # Check within requested time range
            if slot_start < start_time or slot_end > end_time:
                return False
                
            # Check for overlaps with existing events
            for busy in busy_slots:
                busy_start = datetime.fromisoformat(busy["start"]).replace(tzinfo=None)
                busy_end = datetime.fromisoformat(busy["end"]).replace(tzinfo=None)
                if (slot_start < busy_end and slot_end > busy_start):
                    return False
                
                # Check for minimum buffer between events (15 minutes)
                buffer = timedelta(minutes=15)
                if abs(slot_start - busy_end) < buffer or abs(slot_end - busy_start) < buffer:
                    return False
                    
            return True
        except Exception as e:
            print(f"Claude API error: {str(e)}")
            return False

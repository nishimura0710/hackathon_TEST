import React, { useState } from 'react';

export const CalendarScheduler: React.FC = () => {
  const [startTime, setStartTime] = useState('');
  const [endTime, setEndTime] = useState('');
  const [summary, setSummary] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  const handleSchedule = async () => {
    try {
      setIsLoading(true);
      setError(null);
      setSuccess(null);

      if (!startTime || !endTime) {
        throw new Error('開始時刻と終了時刻を入力してください');
      }

      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/calendar/schedule`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          start_time: new Date(startTime).toISOString(),
          end_time: new Date(endTime).toISOString(),
          summary: summary || 'Scheduled Meeting'
        }),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || 'スケジュール登録に失敗しました');
      }

      const data = await response.json();
      setSuccess('スケジュールを登録しました');
      if (data.event_link) {
        window.open(data.event_link, '_blank');
      }
      
      // Reset form
      setStartTime('');
      setEndTime('');
      setSummary('');
    } catch (err) {
      console.error('Error scheduling event:', err);
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError('予期せぬエラーが発生しました。もう一度お試しください。');
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center gap-4 p-4">
      <h2 className="text-xl font-bold">スケジュール登録</h2>
      <div className="flex flex-col gap-4 w-full max-w-md">
        <div className="flex flex-col gap-2">
          <label htmlFor="summary" className="text-sm font-medium">
            タイトル
          </label>
          <input
            id="summary"
            type="text"
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="ミーティング"
            className="px-3 py-2 border rounded"
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor="startTime" className="text-sm font-medium">
            開始時刻
          </label>
          <input
            id="startTime"
            type="datetime-local"
            value={startTime}
            onChange={(e) => setStartTime(e.target.value)}
            className="px-3 py-2 border rounded"
          />
        </div>
        <div className="flex flex-col gap-2">
          <label htmlFor="endTime" className="text-sm font-medium">
            終了時刻
          </label>
          <input
            id="endTime"
            type="datetime-local"
            value={endTime}
            onChange={(e) => setEndTime(e.target.value)}
            className="px-3 py-2 border rounded"
          />
        </div>
        {error && (
          <p className="text-red-500 text-sm">{error}</p>
        )}
        {success && (
          <p className="text-green-500 text-sm">{success}</p>
        )}
        <button
          onClick={handleSchedule}
          disabled={isLoading}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50"
        >
          {isLoading ? '処理中...' : '登録'}
        </button>
      </div>
    </div>
  );
};

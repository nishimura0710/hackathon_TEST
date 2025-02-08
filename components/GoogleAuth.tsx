import { useEffect, useState } from 'react';

export const GoogleAuth = () => {
  const [error, setError] = useState<string | null>(null);

  const handleGoogleLogin = async () => {
    try {
      const response = await fetch(`${process.env.VITE_API_URL}/auth/google`, {
        method: 'GET',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        throw new Error('Failed to authenticate');
      }

      const data = await response.json();
      if (data.auth_url) {
        window.location.href = data.auth_url;
      }
    } catch (err) {
      setError('エラーが発生しました。もう一度お試しください。');
      console.error('Error during authentication:', err);
    }
  };

  return (
    <div>
      <h1>カレンダー連携</h1>
      {error && <p>{error}</p>}
      <button onClick={handleGoogleLogin}>Googleでログイン</button>
    </div>
  );
};

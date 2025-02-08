import { useEffect, useState } from 'react';

interface GoogleAuthProps {
  onAuthSuccess: () => void;
}

export const GoogleAuth: React.FC<GoogleAuthProps> = ({ onAuthSuccess }) => {
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    const handleAuthSuccess = (event: MessageEvent) => {
      if (event.data?.type === 'AUTH_SUCCESS') {
        onAuthSuccess();
      }
    };

    window.addEventListener('message', handleAuthSuccess);
    return () => window.removeEventListener('message', handleAuthSuccess);
  }, [onAuthSuccess]);

  const handleGoogleLogin = async () => {
    try {
      setIsLoading(true);
      setError(null);
      
      const response = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/auth/google`, {
        method: 'GET',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || '認証に失敗しました');
      }

      const data = await response.json();
      if (data.auth_url) {
        const authWindow = window.open(data.auth_url, '_blank', 'width=600,height=800');
        
        // Check for redirect completion
        const checkRedirect = setInterval(() => {
          if (authWindow?.closed) {
            clearInterval(checkRedirect);
            // Check authentication status
            fetch(`${process.env.NEXT_PUBLIC_API_URL}/auth/status`, {
              credentials: 'include'
            }).then(async (response) => {
              if (response.ok) {
                const status = await response.json();
                if (status.authenticated) {
                  onAuthSuccess();
                }
              }
            }).catch(console.error);
          }
        }, 1000);
      } else {
        throw new Error('認証URLが見つかりませんでした');
      }
    } catch (err) {
      console.error('Error during authentication:', err);
      if (err instanceof Error) {
        setError(err.message || '認証エラーが発生しました。もう一度お試しください。');
      } else {
        setError('予期せぬエラーが発生しました。もう一度お試しください。');
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex flex-col items-center gap-4 p-4">
      <h1 className="text-2xl font-bold">カレンダー連携</h1>
      {error && (
        <p className="text-red-500 text-sm">{error}</p>
      )}
      <button
        onClick={handleGoogleLogin}
        disabled={isLoading}
        className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:opacity-50"
      >
        {isLoading ? '処理中...' : 'Googleでログイン'}
      </button>
    </div>
  );
};

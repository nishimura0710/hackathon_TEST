FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app app/

ENV PORT=8000
ENV GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
ENV GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET}
ENV OAUTH_REDIRECT_URI=${BACKEND_URL}/auth/google/callback
ENV REDIS_SSL=true
ENV REDIS_TLS=true
ENV PYTHONPATH=/app
ENV BACKEND_URL=https://backend-app-ikkjfeex-lively-shadow-8911.fly.dev

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

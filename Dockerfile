FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app app/

ENV PORT=8000
ENV PYTHONPATH=/app
ENV BACKEND_URL=https://backend-app-ikkjfeex.fly.dev

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

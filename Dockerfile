FROM python:3.12-slim

WORKDIR /app

RUN pip install fastapi==0.109.0 \
    uvicorn==0.27.0 \
    pydantic==2.5.3 \
    python-dotenv==1.0.0 \
    anthropic==0.45.2 \
    google-auth-oauthlib==1.2.0 \
    google-auth-httplib2==0.2.0 \
    google-api-python-client==2.118.0

COPY app app/

ENV PORT=8080
ENV PYTHONPATH=/app

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

# Portable image (Render Docker, Koyeb, Hugging Face Spaces, Fly, any VM).
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8080
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data ./data
COPY bot.py conversation_handlers.py ./
EXPOSE 8080
# Single worker on purpose: all state is in-process memory.
CMD ["sh", "-c", "uvicorn bot:app --host 0.0.0.0 --port ${PORT} --workers 1 --timeout-keep-alive 75"]

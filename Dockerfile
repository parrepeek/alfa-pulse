FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock
COPY app ./app
COPY scripts ./scripts
COPY tests ./tests
COPY data ./data
EXPOSE 8000
# при первом запуске сервис сам загрузит профили, индекс аналитики и тестовую ленту в SQLite (data/alfa_pulse.sqlite3)
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]

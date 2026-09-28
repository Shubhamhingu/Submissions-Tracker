FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -u 8888 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# CMD ["python", "main.py"]
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "app:app"]
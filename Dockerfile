FROM mcr.microsoft.com/playwright/python:v1.52.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN playwright install chromium

COPY . .

CMD ["python", "goethe_booker_india.py", "--headless", "--exam", "b2", "--env-only"]

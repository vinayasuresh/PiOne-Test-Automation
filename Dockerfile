FROM mcr.microsoft.com/playwright/python:v1.64.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . ./

# The named Docker volumes receive these writable directories on first use.
RUN mkdir -p automation_framework/reports automation_framework/logs scans \
    && chown -R pwuser:pwuser /app

USER pwuser

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

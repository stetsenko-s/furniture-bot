FROM python:3.13-slim
WORKDIR /app
COPY bot/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY contracts /app/contracts
COPY infrastructure /app/infrastructure
COPY bot /app/bot
COPY run_bot.py /app/run_bot.py
CMD ["python", "run_bot.py"]

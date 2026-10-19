FROM python:3.13-slim
WORKDIR /app
COPY services/dialogue/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY contracts /app/contracts
COPY infrastructure /app/infrastructure
COPY services/__init__.py /app/services/__init__.py
COPY services/dialogue /app/services/dialogue
COPY run_dialogue.py /app/run_dialogue.py
CMD ["python", "run_dialogue.py"]

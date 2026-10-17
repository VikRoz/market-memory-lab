FROM python:3.12.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY source/requirements.lock /tmp/requirements.lock
RUN python -m pip install --only-binary=:all: --require-hashes --no-deps --no-cache-dir -r /tmp/requirements.lock && python -m pip check
COPY source/ /app/
COPY container_main.py /app/judge_entry.py
RUN python -m demo.sdk_contract
ENTRYPOINT ["timeout", "--signal=KILL", "180s", "python", "/app/judge_entry.py"]

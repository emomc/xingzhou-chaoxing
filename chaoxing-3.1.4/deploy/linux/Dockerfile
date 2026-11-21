FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/chaoxing \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/chaoxing
COPY requirements.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY api ./api
COPY resource ./resource
COPY main.py webai_check.py ./
WORKDIR /state
ENTRYPOINT ["python"]
CMD ["/opt/chaoxing/main.py", "-c", "/state/config.ini"]

ARG PYTHON_VERSION=3.8
FROM python:${PYTHON_VERSION}-slim
WORKDIR /work
COPY requirements-test.txt requirements-runtime.lock /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-test.txt
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "-m", "pytest", "-p", "no:cacheprovider"]

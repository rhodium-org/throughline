# throughline as a portable CI gate: `docker run ... tl -C /work check --strict`.
FROM python:3.12-slim

WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

WORKDIR /work
ENTRYPOINT ["tl"]
CMD ["check"]

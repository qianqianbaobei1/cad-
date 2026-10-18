# Multi-stage build: compile GNU LibreDWG from source to obtain dwg2dxf and dwg2SVG
FROM gcc:bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    wget make texinfo ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 源码包按固定 SHA256 校验（已用 GNU 公钥环验过维护者签名），不一致即构建失败
RUN wget -q https://ftp.gnu.org/gnu/libredwg/libredwg-0.12.5.tar.gz \
    && echo "4b5b38943e4a060bfee34a8542701b26102262610b1dd4dc58d76fadfb995106  libredwg-0.12.5.tar.gz" | sha256sum -c - \
    && tar -xzf libredwg-0.12.5.tar.gz \
    && cd libredwg-0.12.5 \
    && ./configure --prefix=/usr --disable-bindings \
    && make -j$(nproc) \
    && make install

# Runtime stage for Hugging Face Spaces
FROM python:3.11-slim

# Install system dependencies: CJK fonts for CAD drawing rendering and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    fonts-wqy-microhei \
    fonts-wqy-zenhei \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy LibreDWG binaries and shared libraries from builder
COPY --from=builder /usr/bin/dwg* /usr/bin/
COPY --from=builder /usr/lib/libredwg* /usr/lib/
RUN ldconfig

# Set up user according to Hugging Face Spaces guidelines (UID 1000)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend:/app

WORKDIR /app

# Install python dependencies
COPY --chown=user backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --user -r /app/backend/requirements.txt

# Copy all application code
COPY --chown=user . /app

# Ensure runtime directories exist
RUN mkdir -p /app/backend/work /app/backend/data /app/deliveries

# Expose default Hugging Face Spaces port
EXPOSE 7860

# Launch FastAPI server on 0.0.0.0:7860
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "7860"]

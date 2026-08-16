FROM python:3.12-slim

WORKDIR /app

# uv 是 Astral 出品的 Rust 实现的 Python 包管理器，比 pip 快 10x
RUN pip install --no-cache-dir uv

# 复制依赖文件
COPY pyproject.toml ./
# 注意：项目不锁定 uv.lock（开发期用 uv sync 自由解析），不 COPY 锁文件

# 安装依赖（不装项目本身，只装 deps，避免源码变动触发重装）
# --no-deps 跳过 self-install：hatchling build 时需要 app/ 目录，而 app/ 此时还没 COPY
RUN uv pip install --system --no-cache --no-deps .

# 复制源码
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
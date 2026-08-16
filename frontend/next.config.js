/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 允许 dev server 通过代理 WS 到 8000；如果用直接连接，把 NEXT_PUBLIC_WS_URL 设为 ws://localhost:8000
};

module.exports = nextConfig;
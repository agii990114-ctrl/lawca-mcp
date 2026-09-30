import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 개발 서버는 /api 를 백엔드(8000)로 넘긴다. 운영에서는 FastAPI가 빌드 결과(dist)를 같은 오리진으로 서빙한다.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } } },
});

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// dev proxy：前端 :5173 的 /api 转发到后端 :8000，开发与生产同源，免 CORS（计划 5.4）。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
});

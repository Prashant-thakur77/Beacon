import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { nodePolyfills } from "vite-plugin-node-polyfills";

// The Transcribe streaming SDK's event-stream marshaller expects Buffer/process.
export default defineConfig({
  plugins: [react(), nodePolyfills({ include: ["buffer", "process", "util", "stream", "events"] })],
  build: { outDir: "dist", sourcemap: false, target: "es2020" },
  server: { port: 5173 },
});

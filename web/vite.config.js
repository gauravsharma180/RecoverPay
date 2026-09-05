import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built assets are served by FastAPI from web/dist, so paths stay relative.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { port: 5173 },
});

import { defineConfig } from 'vite';

// base './' keeps every asset and bundle URL relative, so dist/ works from any static file server or sub-path.
export default defineConfig({
  base: './',
  server: { fs: { allow: ['..'] } }, // tokens.css and dba.css are imported from ../design
  build: { outDir: 'dist', emptyOutDir: true },
});

// External gateway operations have been split into the ./Gateway/ directory (shell + tenants tab + logs tab + create modal + shared metric definitions).
// This file only re-exports, preserving the import path in App.tsx.
export { AdminGateway } from './Gateway';

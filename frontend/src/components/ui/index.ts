// Unified export for the UI component layer. New pages take components from here; don't hand-write `px-3 py-1.5 rounded-md`
// plus inline style anymore -- spec in docs/03-系统设计/前端UI规范.md.
export { Button, type ButtonProps, type ButtonVariant, type ButtonSize } from './Button';
export { Badge, StatusDot, type BadgeTone } from './Badge';
export { Card, PageHeader } from './Card';
export { Modal } from '../overlays/Modal';
export { Input, Textarea, Field, type InputProps, type TextareaProps } from './Input';
export { PillTabs, type PillTabItem } from './Tabs';
export { TableShell, StickyHead, SkeletonRows, EmptyState, EmptyRow } from './DataTable';
export { SectionCard } from './SectionCard';
export { UiSelect, UiMultiSelect, type UiSelectOption } from './Select';
export { LoadingBlock } from './Loading';
export { toast } from './toast';
export { uiConfirm, uiAlert, uiPrompt } from './dialogs';
export { openCommandPalette } from './commandPaletteBus';

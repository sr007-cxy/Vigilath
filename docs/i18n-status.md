# Frontend Internationalization Status

## Scope

The React SPA supports Simplified Chinese (`zh`) and English (`en`). Translation resources and locale selection live under `frontend/src/`.

## Current behavior

- The selected locale is persisted in browser storage.
- New UI copy must be added to both locale dictionaries.
- API values, brand names, URLs, and code identifiers are not translated.
- Missing keys should fall back to English and be reported during review.

## Review checklist

1. Add the key to both language resources.
2. Verify desktop and mobile layouts for longer English strings.
3. Check date, number, currency, and plural formatting.
4. Confirm that browser refresh preserves the selected locale.
5. Run the frontend lint and production build.

This file is a maintenance checklist, not a specification for a third-party translation service.

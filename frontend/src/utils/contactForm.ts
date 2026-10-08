// The single submit path for the contact form. The result page used to have its own "fake submit" (it only showed a success toast
// without sending a request), so leads were silently dropped - see issue #17. All new entry points must go through here.
export const sanitizeContactInput = (input: string): string =>
  input
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

export interface ContactPayload {
  name: string;
  email: string;
  website: string;
  message?: string;
}

/** POST /api/contact - writes contact_submissions and triggers the confirmation email. Throws on failure; the caller shows the message. */
export async function submitContact(payload: ContactPayload): Promise<void> {
  const res = await fetch('/api/contact', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: sanitizeContactInput(payload.name),
      email: sanitizeContactInput(payload.email),
      website: sanitizeContactInput(payload.website),
      message: payload.message?.trim() ? sanitizeContactInput(payload.message) : undefined,
    }),
  });
  if (!res.ok) throw new Error(`contact submit failed: ${res.status}`);
}

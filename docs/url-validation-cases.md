# URL Validation Cases (Frontend/Backend Contract)

The frontend `frontend/src/utils/validateUrl.ts` and backend `backend/geo/utils/validator.py::validate_url`
must return the same pass/reject result for every input. Update this list before changing a rule, then update both implementations.

## Rules

1. Input may omit a scheme; the frontend adds `https://` before validation. The backend accepts only the normalized string.
2. The scheme must be `http` or `https`.
3. The host must use **ASCII LDH** characters (letters, digits, hyphens, and dots) and contain at least one dot.
4. The host and every label must not start or end with `-` or `.`, and every label must be at least one character.
5. **IDN and non-ASCII hosts are not supported.** Chinese and emoji domains are rejected. To add support, use
   `idna.encode` on the backend and the punycode form of `URL.hostname` on the frontend, then update this list.
6. IPv4 such as `192.168.1.1` passes because it contains only ASCII digits and dots. IPv6 is not supported (it contains `:`,
   which LDH disallows)。
7. Ports, paths, and queries are allowed; host validation examines only the hostname.

## Valid (must pass)

| Input | Notes |
|---|---|
| `example.com` | bare domain; frontend adds `https://` |
| `https://example.com` | full URL |
| `http://example.com` | http scheme |
| `https://example.com/path?q=1` | with path and query |
| `https://sub.example.com` | subdomain |
| `https://example.com:8080` | with port |
| `moltspay.com` | real example; checked end to end |
| `192.168.1.1` | IPv4; ASCII digits and dots only |
| `https://a-b.c-d.com` | LDH with `-` |

## Invalid (must reject)

| Input | Reason |
|---|---|
| empty string | empty |
| `hello` | no dot |
| `超响应` | non-ASCII |
| `https://超响应` | non-ASCII host |
| `淘宝.中国` | IDN; not supported |
| `ftp://example.com` | scheme is not http/https |
| `https://` | no host |
| `https://.com` | empty label |
| `https://example-.com` | label ends with `-` |
| `https://-example.com` | label starts with `-` |
| `javascript:alert(1)` | http/https is required |
| `https://[::1]` | IPv6; contains non-LDH characters |
| `https://example .com` | contains spaces |

## Test procedure

Run the same list once against each implementation:

- backend: `cd backend && .venv/bin/python -c "from geo.utils.validator import validate_url; ..."`
- frontend: `cd frontend && node -e "import('./src/utils/validateUrl.ts')..."`(or use tsx)

The results must match line by line.

# Ziniao Browser Behavior

## Auto-fill: Passwords & 2FA

Ziniao browser has a built-in password manager and 2FA handler. When you encounter a login page (e.g. Amazon Seller Central, Noon, etc.):

1. **Do NOT ask the user for passwords.** Ziniao auto-fills saved credentials automatically.
2. **Inspecting the page does NOT show auto-filled values** — input fields appear empty even when filled.
3. **Read a field's value via browser-use** to check if it has been filled (passwords are redacted automatically).
4. If the value is non-empty, **click the Sign-in / Submit button directly** — do NOT re-type credentials.
5. For **2FA/OTP prompts**, wait 5 seconds for Ziniao to fetch and fill the code, then read the field's value to confirm, then click Submit.
6. Only ask the user for credentials if reading the value confirms fields are still empty after 10+ seconds.

See the browser-use skill for the exact commands to inspect the page,
read a field's value, and click elements.

## Example: Amazon Seller Central Login

Neutral workflow (see the browser-use skill for exact commands):

1. Navigate to `https://sellercentral.amazon.com/home` (redirects to
   sign-in).
2. Wait ~3s for Ziniao auto-fill.
3. Inspect the page to find the password input.
4. Read its value — non-empty means the password is filled.
5. Click the Sign-in button.
6. On the OTP page, wait ~5s for Ziniao to fetch the OTP.
7. Inspect the page to find the OTP input, read its value to confirm the
   code arrived.
8. Click Submit.

## Bookmarks

Ziniao encrypts its Bookmarks file on disk — bookmarks are not readable from the filesystem. If you need the seller center URL, check the store profile context or ask the user.

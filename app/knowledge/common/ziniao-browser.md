# Ziniao Browser Behavior

## Auto-fill: Passwords & 2FA

Ziniao browser has a built-in password manager and 2FA handler. When you encounter a login page (e.g. Amazon Seller Central, Noon, etc.):

1. **Do NOT ask the user for passwords.** Ziniao auto-fills saved credentials automatically.
2. **`browser-use state` does NOT show auto-filled values** — input fields appear empty even when filled.
3. **Use `browser-use get value <index>`** to check if a field has been filled (passwords are redacted automatically).
4. If the value is non-empty, **click the Sign-in / Submit button directly** — do NOT re-type credentials.
5. For **2FA/OTP prompts**, wait 5 seconds for Ziniao to fetch and fill the code, then `browser-use get value` to confirm, then click Submit.
6. Only ask the user for credentials if `get value` confirms fields are still empty after 10+ seconds.

## Example: Amazon Seller Central Login

```bash
browser-use open https://sellercentral.amazon.com/home   # redirects to sign-in
sleep 3                                                   # wait for auto-fill
browser-use state                     # find password input index, e.g. [4]
browser-use get value 4               # non-empty → password is filled
browser-use click 17                  # click Sign-in button
# On OTP page:
sleep 5                               # wait for Ziniao to fetch OTP
browser-use state                     # find OTP input index, e.g. [1447]
browser-use get value 1447            # returns the OTP code
browser-use click <submit-index>      # click Submit
```

## Bookmarks

Ziniao encrypts its Bookmarks file on disk — bookmarks are not readable from the filesystem. If you need the seller center URL, check the store profile context or ask the user.

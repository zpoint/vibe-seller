"""IMAP and SMTP server auto-discovery from email domain."""

KNOWN_IMAP_SERVERS: dict[str, tuple[str, int]] = {
    # NetEase
    '163.com': ('imap.163.com', 993),
    '126.com': ('imap.126.com', 993),
    'yeah.net': ('imap.yeah.net', 993),
    # Google
    'gmail.com': ('imap.gmail.com', 993),
    'googlemail.com': ('imap.gmail.com', 993),
    # Microsoft
    'outlook.com': ('outlook.office365.com', 993),
    'hotmail.com': ('outlook.office365.com', 993),
    'live.com': ('outlook.office365.com', 993),
    # Chinese providers
    'qq.com': ('imap.qq.com', 993),
    'foxmail.com': ('imap.qq.com', 993),
    'sina.com': ('imap.sina.com', 993),
    'sohu.com': ('imap.sohu.com', 993),
    'aliyun.com': ('imap.aliyun.com', 993),
    # Yahoo
    'yahoo.com': ('imap.mail.yahoo.com', 993),
    'yahoo.co.jp': ('imap.mail.yahoo.co.jp', 993),
}

# (host, port, use_starttls)
# use_starttls=True means SMTP+STARTTLS; False means SMTP_SSL
KNOWN_SMTP_SERVERS: dict[str, tuple[str, int, bool]] = {
    # NetEase
    '163.com': ('smtp.163.com', 465, False),
    '126.com': ('smtp.126.com', 465, False),
    'yeah.net': ('smtp.yeah.net', 465, False),
    # Google
    'gmail.com': ('smtp.gmail.com', 587, True),
    'googlemail.com': ('smtp.gmail.com', 587, True),
    # Microsoft
    'outlook.com': ('smtp.office365.com', 587, True),
    'hotmail.com': ('smtp.office365.com', 587, True),
    'live.com': ('smtp.office365.com', 587, True),
    # Chinese providers
    'qq.com': ('smtp.qq.com', 465, False),
    'foxmail.com': ('smtp.qq.com', 465, False),
    'sina.com': ('smtp.sina.com', 465, False),
    'sohu.com': ('smtp.sohu.com', 465, False),
    'aliyun.com': ('smtp.aliyun.com', 465, False),
    # Yahoo
    'yahoo.com': ('smtp.mail.yahoo.com', 465, False),
    'yahoo.co.jp': ('smtp.mail.yahoo.co.jp', 465, False),
}


def discover_imap(email: str) -> tuple[str, int, str] | None:
    """Return (host, port, source) or None.

    source is 'known', 'heuristic', etc.
    """
    domain = email.rsplit('@', 1)[-1].lower()
    entry = KNOWN_IMAP_SERVERS.get(domain)
    if entry:
        return (entry[0], entry[1], 'known')
    # Heuristic fallback
    return (f'imap.{domain}', 993, 'heuristic')


def discover_smtp(
    email: str,
) -> tuple[str, int, bool, str] | None:
    """Return (host, port, use_starttls, source) or None.

    use_starttls: True = STARTTLS on port 587,
                  False = SMTP_SSL on port 465.
    """
    domain = email.rsplit('@', 1)[-1].lower()
    entry = KNOWN_SMTP_SERVERS.get(domain)
    if entry:
        return (entry[0], entry[1], entry[2], 'known')
    # Heuristic fallback
    return (f'smtp.{domain}', 465, False, 'heuristic')

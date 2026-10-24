"""Shell-command shapes shared by the Bash guards (a leaf module)."""

# Match a program only in *command position* — not as an argument
# (``grep pkill file``) or inside a string (``echo "pkill -f x"``).
# Command position = start of input, after a shell separator (``;``,
# ``&&``, ``||``, ``|``, ``&``, newline) or the opener of a
# subshell/command-substitution (``(``, backtick), optionally preceded
# by ``sudo`` / ``time`` / similar leading words. Quote handling is
# ad-hoc — a literal that survives shell quoting and lands at the start
# of a real command will be caught; one *inside* a matching pair of
# quotes won't be in command position so it's not matched.
COMMAND_PREFIX = r'(?:^|[;&|\n(`])\s*(?:(?:sudo|time|exec|nohup)\s+)*'

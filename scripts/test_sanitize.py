"""Validate labeler output sanitisation against the exact garbage a weak local model produced."""

from daylog.label import _clean_name, _clean_steps, _clean_text, _is_degenerate

# The real failure from the screenshot: field name leaked into the value.
assert _clean_name("name: 'Configure and run daylog project'") == "Configure and run daylog project"
assert _clean_name('"  Set up Cloudflare  "') == "Set up Cloudflare"
assert _clean_name("") == "Untitled task"

# The runaway-number step: salvage the readable head, drop the number spew.
runaway = "Navigated to the daylog project directory " + ", ".join(str(n) for n in range(6, 247))
cleaned = _clean_text(runaway)
assert cleaned == "Navigated to the daylog project directory", repr(cleaned)
assert not _is_degenerate(cleaned)

# A purely degenerate step (all numbers) is dropped entirely.
assert _is_degenerate("1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12")
assert _is_degenerate("done done done done done done")

# Step list: keep good ones, drop junk, dedupe, cap.
steps = [
    "Opened the Zero Trust dashboard",
    "Navigated to the daylog project directory " + ", ".join(str(n) for n in range(1, 200)),
    "1 2 3 4 5 6 7 8 9 10 11 12 13",
    "Opened the Zero Trust dashboard",          # dup
    "Fixed the DNS record",
]
out = _clean_steps(steps)
assert "Opened the Zero Trust dashboard" in out
assert "Fixed the DNS record" in out
assert "Navigated to the daylog project directory" in out      # salvaged head
assert out.count("Opened the Zero Trust dashboard") == 1       # deduped
assert all(not _is_degenerate(s) for s in out)
assert len(out) <= 8

print("OK: labeler sanitisation cleans names, salvages steps, drops degenerate output")

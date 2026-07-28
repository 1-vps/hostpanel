#!/usr/bin/env python3
import pathlib
import re
import urllib.parse
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


class DocumentationLinkTests(unittest.TestCase):
    def test_relative_markdown_links_exist(self):
        missing = []
        for document in sorted(ROOT.rglob("*.md")):
            if ".git" in document.parts:
                continue
            text = document.read_text(encoding="utf-8")
            for raw_target in LINK_PATTERN.findall(text):
                target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
                if not target or target.startswith("#"):
                    continue
                parsed = urllib.parse.urlparse(target)
                if parsed.scheme or parsed.netloc:
                    continue
                relative = urllib.parse.unquote(parsed.path)
                if not relative or "${" in relative or "{{" in relative:
                    continue
                resolved = (document.parent / relative).resolve()
                if not resolved.exists():
                    missing.append(
                        f"{document.relative_to(ROOT)} -> {target}"
                    )
        self.assertEqual(missing, [], "Missing local Markdown links:\n" + "\n".join(missing))


if __name__ == "__main__":
    unittest.main()

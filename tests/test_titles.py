"""Compile and exercise the title formatter used by the real GRUB renderer."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("cc"), "Title tests require a C compiler")
class TitleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="caelestia-titles-")
        cls.addClassCleanup(cls.directory.cleanup)
        patch = (ROOT / "src/grub.patch").read_text()
        source = patch.split("+++ b/grub-core/gfxmenu/carousel.inc\n", 1)[1]
        source = source.split("\n--- a/", 1)[0]
        source = "\n".join(line[1:] for line in source.splitlines()[1:])
        formatter = source.split("static char *\ncarousel_get_title", 1)[1]
        formatter = (
            "static char *\ncarousel_get_title" + formatter.split("\nstatic void", 1)[0]
        )
        driver = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef size_t grub_size_t;
typedef int grub_font_t;
#define grub_strlen strlen
#define grub_strcpy strcpy
static void *grub_malloc(size_t size) {
  return getenv("FAIL_ALLOCATION") ? NULL : malloc(size);
}
/* Variable glyph widths make byte-count truncation visibly incorrect. */
static int grub_font_get_string_width(grub_font_t font, const char *text) {
  int width = 0;
  (void)font;
  for (; *text; text++) {
    unsigned char c = (unsigned char)*text;
    if (c < 128) width += 8;
    else if ((c & 0xc0) != 0x80) width += 16;
  }
  return width;
}
"""
        driver += (
            formatter
            + r"""
int main(int argc, char **argv) {
  char *original = strdup(argv[1]);
  char *title = carousel_get_title(0, original, atoi(argv[2]));
  (void)argc;
  if (!title) { free(original); return 2; }
  puts(title);
  puts(original);
  free(title);
  free(original);
  return 0;
}
"""
        )
        c_file = Path(cls.directory.name) / "title.c"
        c_file.write_text(driver)
        cls.binary = c_file.with_suffix("")
        subprocess.run(
            ["cc", "-Wall", "-Wextra", "-Werror", str(c_file), "-o", str(cls.binary)],
            check=True,
        )

    def get_title(self, title, width):
        result = subprocess.run(
            [str(self.binary), title, str(width)],
            check=True,
            capture_output=True,
            text=True,
        )
        rendered, original = result.stdout.splitlines()
        self.assertEqual(
            original, title, "Display formatting must not mutate the boot entry"
        )
        return rendered

    def test_fitting_title_is_preserved(self):
        self.assertEqual(self.get_title("Windows", 56), "Windows")
        self.assertEqual(
            self.get_title("Windows Boot Manager", 160), "Windows Boot Manager"
        )

    def test_long_windows_title_keeps_its_readable_prefix(self):
        self.assertEqual(
            self.get_title("Windows Boot Manager (on /dev/nvme0n1p1)", 112),
            "Windows Boo...",
        )

    def test_truncation_keeps_complete_utf8_characters(self):
        self.assertEqual(self.get_title("启动 Windows 系统", 80), "启动 Wi...")

    def test_allocation_failure_returns_without_touching_source(self):
        import os

        result = subprocess.run(
            [str(self.binary), "Windows", "56"],
            env={**os.environ, "FAIL_ALLOCATION": "1"},
            check=False,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 2)

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BUILD_SCRIPT = Path(__file__).parents[1] / "BUILD" / "build.py"
SPEC = importlib.util.spec_from_file_location("terranore_build", BUILD_SCRIPT)
build_driver = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(build_driver)


class BuildPublicationTests(unittest.TestCase):
    def test_publication_failure_restores_old_output(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            output = parent / "output"
            staging = parent / "staging"
            output.mkdir()
            staging.mkdir()
            (output / "version.txt").write_text("old", encoding="utf-8")
            (staging / "version.txt").write_text("new", encoding="utf-8")
            real_rename = build_driver._rename

            def fail_staging_publication(source, destination):
                if source == staging and destination == output:
                    raise PermissionError("artificial publication failure")
                real_rename(source, destination)

            with patch.object(build_driver, "_rename", side_effect=fail_staging_publication):
                with self.assertRaisesRegex(build_driver.BuildFailure, "output восстановлен"):
                    build_driver.publish(staging, output)

            self.assertEqual((output / "version.txt").read_text(encoding="utf-8"), "old")
            self.assertEqual((staging / "version.txt").read_text(encoding="utf-8"), "new")
            self.assertFalse((parent / ".output.backup").exists())

    def test_successful_publication_removes_backup(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            output = parent / "output"
            staging = parent / "staging"
            output.mkdir()
            staging.mkdir()
            (output / "version.txt").write_text("old", encoding="utf-8")
            (staging / "version.txt").write_text("new", encoding="utf-8")

            build_driver.publish(staging, output)

            self.assertEqual((output / "version.txt").read_text(encoding="utf-8"), "new")
            self.assertFalse(staging.exists())
            self.assertFalse((parent / ".output.backup").exists())


if __name__ == "__main__":
    unittest.main()

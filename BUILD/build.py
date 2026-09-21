#!/usr/bin/env python3
"""Reproducible, standard-library-only TerraNore test build driver."""

from __future__ import annotations

import argparse
import compileall
import py_compile
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "BUILD" / "out" / "TerraNore-Test"
LOG_DIR = ROOT / "BUILD" / "logs"


class BuildFailure(RuntimeError):
    """A build error suitable for displaying without a traceback."""


def log(message: str, stream) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {message}"
    print(line, flush=True)
    print(line, file=stream, flush=True)


def copy_sources(destination: Path) -> None:
    ignored = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
    shutil.copytree(ROOT / "src", destination / "src", ignore=ignored)
    for name in ("README.md",):
        shutil.copy2(ROOT / name, destination / name)


def write_launchers(destination: Path) -> None:
    (destination / "run.cmd").write_text(
        "@echo off\r\nsetlocal\r\ncd /d \"%~dp0\"\r\npy -3 -m src.server %*\r\n",
        encoding="utf-8",
    )
    launcher = destination / "run.sh"
    launcher.write_text(
        '#!/usr/bin/env sh\nset -eu\ncd "$(dirname "$0")"\nexec "${PYTHON:-python3}" -m src.server "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)


def check_staging(staging: Path) -> None:
    """Reject an incomplete distribution before touching the live output."""
    required = (
        staging / "src" / "simulation" / "engine.py",
        staging / "src" / "ui" / "index.html",
        staging / "run.cmd",
        staging / "run.sh",
        staging / "logs",
        staging / "saves",
    )
    missing = [path.relative_to(staging) for path in required if not path.exists()]
    if missing:
        names = ", ".join(str(path) for path in missing)
        raise BuildFailure(f"проверка staging не пройдена; отсутствуют: {names}")


def _rename(source: Path, destination: Path) -> None:
    """A small seam for testing failures in the publication transaction."""
    source.replace(destination)


def publish(staging: Path, output: Path) -> None:
    """Publish staging while preserving (and, on failure, restoring) output."""
    backup = output.with_name(f".{output.name}.backup")
    had_output = output.exists()

    if backup.exists():
        raise BuildFailure(
            f"не удалось начать публикацию: резервный каталог уже существует: {backup}. "
            "Рабочая сборка не изменена"
        )

    if had_output:
        try:
            _rename(output, backup)
        except OSError as error:
            raise BuildFailure(
                "не удалось переименовать рабочий output в backup. Рабочая сборка "
                "не удалена; на Windows остановите запущенный из неё сервер и повторите "
                f"сборку ({error})"
            ) from error

    try:
        _rename(staging, output)
    except OSError as publish_error:
        if had_output:
            try:
                _rename(backup, output)
            except OSError as restore_error:
                raise BuildFailure(
                    "ошибка публикации и автоматического восстановления. Старая сборка "
                    f"сохранена в {backup}; не удаляйте её ({restore_error})"
                ) from publish_error
            raise BuildFailure(
                "не удалось опубликовать staging; прежний output восстановлен. "
                "На Windows остановите сервер, который может блокировать файлы, и "
                f"повторите сборку ({publish_error})"
            ) from publish_error
        raise BuildFailure(f"не удалось опубликовать staging: {publish_error}") from publish_error

    if had_output:
        try:
            shutil.rmtree(backup)
        except OSError as error:
            raise BuildFailure(
                f"новая сборка опубликована, но резервный каталог {backup} не удалось "
                f"удалить: {error}"
            ) from error


def build(output: Path, stream) -> Path:
    if sys.version_info < (3, 10):
        raise BuildFailure("требуется Python 3.10 или новее")
    if not (ROOT / "src" / "simulation" / "engine.py").is_file():
        raise BuildFailure("не найдено ядро src/simulation/engine.py")
    if not (ROOT / "src" / "ui" / "index.html").is_file():
        raise BuildFailure("не найден интерфейс src/ui/index.html")

    output = output.resolve()
    if output == ROOT or ROOT in output.parents and output.parts[: len(ROOT.parts) + 1] != (*ROOT.parts, "BUILD"):
        raise BuildFailure("выходной каталог должен находиться в BUILD или вне дерева проекта")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        log(f"Инструменты: Python {sys.version.split()[0]} ({sys.executable})", stream)
        log("Сборка ядра симуляции (проверка bytecode)", stream)
        copy_sources(staging)
        if not compileall.compile_dir(
            staging / "src", quiet=1, force=True,
            stripdir=str(staging), prependdir=".",
            invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH,
        ):
            raise BuildFailure("компиляция Python-модулей завершилась ошибкой")
        log("HTML-интерфейс скопирован", stream)
        write_launchers(staging)
        (staging / "logs").mkdir()
        (staging / "saves").mkdir()
        check_staging(staging)
        log(f"Staging проверен: {staging}", stream)
        publish(staging, output)
        log(f"Тестовый дистрибутив готов: {output}", stream)
        return output
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Сборка TerraNore Test и запуск локального сервера")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--build-only", action="store_true", help="не запускать сервер после сборки")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "build.log"
    try:
        with log_path.open("a", encoding="utf-8") as stream:
            log("Начало тестовой сборки", stream)
            output = build(args.output, stream)
            if args.build_only:
                return 0
            command = [sys.executable, "-m", "src.server", "--port", str(args.port)]
            if args.no_browser:
                command.append("--no-browser")
            log("Локальный сервер подготовлен; запуск на loopback-интерфейсе", stream)
            server_log = output / "logs" / "server.log"
            with server_log.open("a", encoding="utf-8") as server_stream:
                return subprocess.call(command, cwd=output, stdout=server_stream, stderr=subprocess.STDOUT)
    except (BuildFailure, OSError, subprocess.SubprocessError) as error:
        print(f"ОШИБКА СБОРКИ: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

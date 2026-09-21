import argparse
import threading
import webbrowser

from .app import create_server


def main() -> None:
    parser = argparse.ArgumentParser(description="TerraNore Test local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--saves-dir", default="saves")
    parser.add_argument("--enable-developer-import", action="store_true",
                        help="allow loading an arbitrary external save path")
    args = parser.parse_args()

    server = create_server(args.host, args.port, args.seed, saves_dir=args.saves_dir,
                           enable_developer_import=args.enable_developer_import)
    url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    print(f"TerraNore Test запущен: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTerraNore Test остановлен.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

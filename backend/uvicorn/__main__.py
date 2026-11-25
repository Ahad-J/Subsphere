from uvicorn import run
import sys

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "app.main:app"
    host = "0.0.0.0"
    port = 8000
    for i, arg in enumerate(sys.argv):
        if arg == "--host" and i + 1 < len(sys.argv):
            host = sys.argv[i + 1]
        elif arg == "--port" and i + 1 < len(sys.argv):
            port = int(sys.argv[i + 1])
    run(target, host=host, port=port)

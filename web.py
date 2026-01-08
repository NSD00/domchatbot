from bot import app
import os

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    print(f"Starting web server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)